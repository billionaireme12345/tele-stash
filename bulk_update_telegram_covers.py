#!/usr/bin/env python3
"""
Bulk-update existing Telegram video covers from the Stash cover audit CSV.

Safety:
- DRY-RUN by default; --apply is required to edit Telegram.
- Never uploads/replaces the video document; it only sets video_cover.
- Backs up each existing Telegram thumbnail before editing.
- Skips a message if its original thumbnail cannot be backed up.
- Processes sequentially and writes a resumable CSV report.
- Message 895 was the successful pilot and is skipped unless --include-tested is used.

Run from the project folder with .venv-wzgram activated.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import os
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pyrogram import Client, raw
from pyrogram.errors import FloodWait

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

CHAT_ID = int(os.environ["TG_CHANNEL_ID"])
API_ID = int(os.environ["TG_API_ID"])
API_HASH = os.environ["TG_API_HASH"]
SESSION_NAME = os.getenv("WZGRAM_SESSION", "wzgram_benchmark")

AUDIT_CSV = BASE_DIR / "stash_cover_audit.csv"
BACKUP_DIR = BASE_DIR / "cover_backups"
REPORT_CSV = BASE_DIR / "telegram_cover_update_report.csv"
PILOT_MESSAGE_ID = 895
DELAY_SECONDS = 1.5

REPORT_FIELDS = [
    "scene_id", "telegram_message_id", "poster_path", "status",
    "backup_path", "original_document_id", "original_document_size",
    "error",
]


def read_audit() -> list[dict[str, str]]:
    if not AUDIT_CSV.is_file():
        raise FileNotFoundError(f"Audit CSV not found: {AUDIT_CSV}")
    rows: list[dict[str, str]] = []
    seen: set[int] = set()
    with AUDIT_CSV.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("status") != "IMAGE_DOWNLOADED":
                continue
            message_id = int(row["telegram_message_id"])
            if message_id in seen:
                raise RuntimeError(f"Duplicate Telegram message ID in audit CSV: {message_id}")
            seen.add(message_id)
            poster = Path(row["downloaded_image"])
            if not poster.is_absolute():
                poster = (BASE_DIR / poster).resolve()
            row["_poster_path"] = str(poster)
            rows.append(row)
    return rows


def load_completed() -> set[int]:
    completed: set[int] = set()
    if not REPORT_CSV.exists():
        return completed
    with REPORT_CSV.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("status") == "UPDATED":
                completed.add(int(row["telegram_message_id"]))
    return completed


def append_report(row: dict[str, Any]) -> None:
    new_file = not REPORT_CSV.exists()
    with REPORT_CSV.open("a", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=REPORT_FIELDS)
        if new_file:
            writer.writeheader()
        writer.writerow({key: row.get(key, "") for key in REPORT_FIELDS})


async def get_raw_video_document(app: Client, peer: Any, message_id: int):
    if not hasattr(peer, "channel_id"):
        raise RuntimeError("TG_CHANNEL_ID did not resolve to a channel.")
    result = await app.invoke(
        raw.functions.channels.GetMessages(
            channel=raw.types.InputChannel(
                channel_id=peer.channel_id,
                access_hash=peer.access_hash,
            ),
            id=[raw.types.InputMessageID(id=message_id)],
        )
    )
    messages = getattr(result, "messages", [])
    message = next(
        (item for item in messages if getattr(item, "id", None) == message_id),
        None,
    )
    media = getattr(message, "media", None)
    document = getattr(media, "document", None)
    if document is None:
        raise RuntimeError("Message does not contain a document.")
    mime = getattr(document, "mime_type", "") or ""
    if not mime.startswith("video/"):
        raise RuntimeError(f"Message document is not video/* (mime={mime!r}).")
    return document


async def backup_original_thumb(app: Client, document_view: Any, message_id: int) -> str:
    thumbs = getattr(document_view, "thumbs", None) or []
    if not thumbs:
        raise RuntimeError("No existing Telegram thumbnail is exposed; refusing to edit without backup.")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup_base = BACKUP_DIR / f"message_{message_id}_original"
    saved = await app.download_media(thumbs[-1], file_name=str(backup_base))
    if not saved or not Path(saved).is_file() or Path(saved).stat().st_size == 0:
        raise RuntimeError("Could not verify the original thumbnail backup.")
    return str(Path(saved).resolve())


async def main() -> None:
    parser = argparse.ArgumentParser(description="Bulk-update Telegram video covers from Stash posters")
    parser.add_argument("--apply", action="store_true", help="Actually edit Telegram message covers")
    parser.add_argument("--include-tested", action="store_true",
                        help="Include pilot message 895 (normally skipped)")
    parser.add_argument("--limit", type=int, default=0,
                        help="Limit number of messages for a small batch; 0 means all")
    parser.add_argument("--delay", type=float, default=DELAY_SECONDS,
                        help="Seconds between messages (default: 1.5)")
    args = parser.parse_args()

    rows = read_audit()
    if args.limit > 0:
        rows = rows[:args.limit]
    completed = load_completed() if args.apply else set()

    print(f"Mode: {'APPLY' if args.apply else 'DRY-RUN'}")
    print(f"Audit rows with downloaded posters: {len(rows)}")
    print(f"Existing successful rows in report: {len(completed)}")
    print(f"Pilot message {PILOT_MESSAGE_ID}: {'included' if args.include_tested else 'skipped'}")
    print("No videos will be re-uploaded.")

    async with Client(
        SESSION_NAME,
        api_id=API_ID,
        api_hash=API_HASH,
        workdir=str(BASE_DIR),
    ) as app:
        peer = await app.resolve_peer(CHAT_ID)
        totals: dict[str, int] = {}

        for index, row in enumerate(rows, start=1):
            scene_id = row["scene_id"]
            message_id = int(row["telegram_message_id"])
            poster = Path(row["_poster_path"])
            result: dict[str, Any] = {
                "scene_id": scene_id,
                "telegram_message_id": message_id,
                "poster_path": str(poster),
                "status": "",
                "backup_path": "",
                "original_document_id": "",
                "original_document_size": "",
                "error": "",
            }

            if message_id == PILOT_MESSAGE_ID and not args.include_tested:
                result["status"] = "SKIPPED_PILOT_ALREADY_UPDATED"
                print(f"[{index}/{len(rows)}] Message {message_id}: skipped (pilot already updated)")
                if args.apply:
                    append_report(result)
                totals[result["status"]] = totals.get(result["status"], 0) + 1
                continue

            if args.apply and message_id in completed:
                print(f"[{index}/{len(rows)}] Message {message_id}: skipped (already UPDATED in report)")
                totals["SKIPPED_ALREADY_UPDATED"] = totals.get("SKIPPED_ALREADY_UPDATED", 0) + 1
                continue

            try:
                if not poster.is_file() or poster.stat().st_size == 0:
                    raise FileNotFoundError(f"Poster missing or empty: {poster}")

                msg = await app.get_messages(CHAT_ID, message_id)
                if not msg or getattr(msg, "empty", False):
                    raise RuntimeError("Telegram message not found.")
                if not getattr(msg, "video", None) and not getattr(msg, "document", None):
                    raise RuntimeError("Message is not exposed as a video/document.")

                raw_document = await get_raw_video_document(app, peer, message_id)
                result["original_document_id"] = raw_document.id
                result["original_document_size"] = getattr(raw_document, "size", "")

                if not args.apply:
                    result["status"] = "DRY_RUN_OK"
                    print(
                        f"[{index}/{len(rows)}] Scene {scene_id} -> message {message_id}: "
                        f"OK, video {raw_document.id}, poster {poster.name}"
                    )
                else:
                    # Back up before attempting any edit. If backup fails, do not proceed.
                    result["backup_path"] = await backup_original_thumb(
                        app, msg.video or msg.document, message_id
                    )
                    # Bind message id explicitly for the edit request.
                    uploaded_file = await app.save_file(str(poster))
                    uploaded = await app.invoke(
                        raw.functions.messages.UploadMedia(
                            peer=peer,
                            media=raw.types.InputMediaUploadedPhoto(file=uploaded_file),
                        )
                    )
                    photo = getattr(uploaded, "photo", None)
                    if photo is None:
                        raise RuntimeError("Telegram did not return an uploaded photo.")
                    input_photo = raw.types.InputPhoto(
                        id=photo.id,
                        access_hash=photo.access_hash,
                        file_reference=photo.file_reference,
                    )
                    input_document = raw.types.InputDocument(
                        id=raw_document.id,
                        access_hash=raw_document.access_hash,
                        file_reference=raw_document.file_reference,
                    )
                    await app.invoke(
                        raw.functions.messages.EditMessage(
                            peer=peer,
                            id=message_id,
                            media=raw.types.InputMediaDocument(
                                id=input_document,
                                video_cover=input_photo,
                            ),
                        )
                    )
                    result["status"] = "UPDATED"
                    print(
                        f"[{index}/{len(rows)}] Scene {scene_id} -> message {message_id}: "
                        f"UPDATED; video document unchanged"
                    )

            except FloodWait as exc:
                result["status"] = "FAILED_FLOOD_WAIT"
                result["error"] = f"Telegram FloodWait: wait {exc.value} seconds; rerun later."
                print(f"[{index}/{len(rows)}] Message {message_id}: {result['error']}")
                if args.apply:
                    append_report(result)
                totals[result["status"]] = totals.get(result["status"], 0) + 1
                break
            except Exception as exc:
                result["status"] = "FAILED"
                result["error"] = f"{type(exc).__name__}: {exc}"
                print(f"[{index}/{len(rows)}] Message {message_id}: FAILED - {result['error']}")

            if args.apply:
                append_report(result)
            totals[result["status"]] = totals.get(result["status"], 0) + 1
            if args.apply and args.delay > 0:
                await asyncio.sleep(args.delay)

    print("\nSUMMARY")
    for status, count in sorted(totals.items()):
        print(f"{status}: {count}")
    if args.apply:
        print(f"Report: {REPORT_CSV}")
        print(f"Backups: {BACKUP_DIR}")
    else:
        print("\nDry-run made no Telegram changes. Review the list, then use --apply.")


if __name__ == "__main__":
    asyncio.run(main())
