
import asyncio
import os
import sqlite3
import sys
from pathlib import Path

from dotenv import load_dotenv
from wzgram import Client

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / ".env")

DB_PATH = BASE / "bridge.sqlite3"
CHANNEL_ID = int(os.getenv("TG_CHANNEL_ID", "0"))
API_ID = int(os.getenv("TG_API_ID", "0"))
API_HASH = os.getenv("TG_API_HASH", "")


async def fetch_video(message_id):
    client = Client(
        "wzgram_benchmark",
        api_id=API_ID,
        api_hash=API_HASH,
        workdir=str(BASE),
        no_updates=True,
        max_concurrent_transmissions=4
    )

    async with client as app:
        result = await app.get_messages(
            CHANNEL_ID,
            message_ids=message_id
        )

        message = (
            result[0] if isinstance(result, list) and result
            else result
        )

        if not message or getattr(message, "empty", False):
            raise RuntimeError(
                f"Telegram message {message_id} was not found."
            )

        media = (
            getattr(message, "video", None)
            or getattr(message, "document", None)
        )

        if not media:
            raise RuntimeError(
                "This Telegram message does not contain video media."
            )

        mime = getattr(media, "mime_type", None) or ""

        if not getattr(message, "video", None):
            if not mime.startswith("video/"):
                raise RuntimeError(
                    "This document is not identified as a video."
                )

        filename = (
            getattr(media, "file_name", None)
            or f"telegram_{message_id}.mp4"
        )

        size = int(getattr(media, "file_size", 0) or 0)
        duration = float(getattr(media, "duration", 0) or 0)
        width = int(getattr(media, "width", 0) or 0)
        height = int(getattr(media, "height", 0) or 0)
        date = str(getattr(message, "date", "") or "")

        norm_name = "".join(
            ch for ch in Path(filename).stem.lower()
            if ch.isalnum()
        )

        return {
            "message_id": int(message.id),
            "filename": filename,
            "norm_name": norm_name,
            "size": size,
            "mime": mime,
            "kind": "video",
            "date": date,
            "duration": duration,
            "width": width,
            "height": height
        }


def save_mapping(scene_id, video):
    with sqlite3.connect(DB_PATH) as db:
        db.execute("BEGIN IMMEDIATE")

        scene_exists = db.execute(
            """
            SELECT message_id, confidence
            FROM scene_telegram_map
            WHERE scene_id=?
            """,
            (scene_id,)
        ).fetchone()

        message_exists = db.execute(
            """
            SELECT scene_id, confidence
            FROM scene_telegram_map
            WHERE message_id=?
            """,
            (video["message_id"],)
        ).fetchone()

        if scene_exists:
            raise RuntimeError(
                f"Scene {scene_id} already maps to message "
                f"{scene_exists[0]} ({scene_exists[1]}). "
                "Nothing changed."
            )

        if message_exists:
            raise RuntimeError(
                f"Message {video['message_id']} already maps to "
                f"scene {message_exists[0]} ({message_exists[1]}). "
                "Nothing changed."
            )

        # Update Telegram media catalogue.
        db.execute(
            """
            INSERT INTO media
            (message_id, filename, norm_name, size, mime, kind, date)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(message_id) DO UPDATE SET
                filename=excluded.filename,
                norm_name=excluded.norm_name,
                size=excluded.size,
                mime=excluded.mime,
                kind=excluded.kind,
                date=excluded.date
            """,
            (
                video["message_id"],
                video["filename"],
                video["norm_name"],
                video["size"],
                video["mime"],
                video["kind"],
                video["date"]
            )
        )

        # Save manual mapping.
        db.execute(
            """
            INSERT INTO scene_telegram_map
            (scene_id, message_id, confidence)
            VALUES (?, ?, 'manual_verified')
            """,
            (scene_id, video["message_id"])
        )

        db.commit()


async def main():
    if not DB_PATH.exists():
        raise FileNotFoundError(DB_PATH)

    if not API_ID or not API_HASH or not CHANNEL_ID:
        raise RuntimeError(
            "Telegram settings are missing from .env"
        )

    print("=" * 45)
    print("       STASH - TELEGRAM MANUAL MAPPER")
    print("=" * 45)

    scene_id = input("\nStash Scene ID: ").strip()
    message_text = input("Telegram message ID: ").strip()

    if not scene_id.isdigit():
        raise ValueError("Scene ID must be numeric.")

    if not message_text.isdigit():
        raise ValueError("Telegram message ID must be numeric.")

    message_id = int(message_text)

    print(f"\nFetching Telegram message {message_id}...")

    video = await fetch_video(message_id)

    print("\nTelegram video found:")
    print(f"  Filename : {video['filename']}")
    print(f"  Size     : {video['size']:,} bytes")
    print(f"  Duration : {video['duration']} seconds")
    print(f"  Video    : {video['width']}x{video['height']}")
    print(f"  Date     : {video['date']}")

    confirm = input(
        f"\nConfirm Scene {scene_id} is this exact video? (y/N): "
    ).strip().lower()

    if confirm != "y":
        print("\nCancelled. Database unchanged.")
        return

    save_mapping(scene_id, video)

    print("\n" + "=" * 45)
    print("MAPPING SAVED SUCCESSFULLY")
    print("=" * 45)
    print(f"Stash Scene    : {scene_id}")
    print(f"Telegram Msg   : {message_id}")
    print("Confidence     : manual_verified")
    print("Catalogue      : Updated")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(f"\nERROR: {exc}")
        sys.exit(1)
