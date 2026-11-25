import argparse
import asyncio
import csv
import json
import os
import re
import sqlite3
import urllib.request
from collections import defaultdict
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

from dotenv import load_dotenv
from wzgram import Client

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / ".env")

DB_PATH = BASE / "bridge.sqlite3"
CHANNEL_ID = int(os.getenv("TG_CHANNEL_ID", "0"))
API_ID = int(os.getenv("TG_API_ID", "0"))
API_HASH = os.getenv("TG_API_HASH", "")
SESSION_NAME = os.getenv("WZGRAM_SESSION", "wzgram_benchmark")
STASH_URL = os.getenv("STASH_URL", "http://127.0.0.1:9999/graphql")

OUTPUT = BASE / "sync_candidates.csv"
REPORT = BASE / "sync_summary.txt"


def normalize(filename):
    return re.sub(
        r"[^a-z0-9]",
        "",
        Path(filename or "").stem.lower()
    )


def stash_graphql(query):
    payload = json.dumps({"query": query}).encode("utf-8")
    request = urllib.request.Request(
        STASH_URL,
        data=payload,
        headers={"Content-Type": "application/json"}
    )

    with urllib.request.urlopen(request, timeout=60) as response:
        result = json.loads(response.read())

    if result.get("errors"):
        raise RuntimeError(result["errors"])

    return result["data"]["findScenes"]


def get_stash_scenes():
    scenes = []

    page = 1
    per_page = 1000
    total = None

    while total is None or len(scenes) < total:
        query = f"""
        query {{
          findScenes(filter: {{page: {page}, per_page: {per_page}}}) {{
            count
            scenes {{
              id
              title
              files {{
                path
                size
                duration
                width
                height
              }}
            }}
          }}
        }}
        """

        data = stash_graphql(query)
        total = data["count"]

        for scene in data["scenes"]:
            for file in scene.get("files") or []:
                filename = Path(file.get("path") or "").name

                scenes.append({
                    "scene_id": str(scene["id"]),
                    "title": scene.get("title") or "",
                    "filename": filename,
                    "norm_name": normalize(filename),
                    "size": int(file.get("size") or 0),
                    "duration": float(file.get("duration") or 0),
                    "width": int(file.get("width") or 0),
                    "height": int(file.get("height") or 0)
                })

        page += 1

    print(f"Stash scenes: {total}")
    print(f"Stash file records: {len(scenes)}")
    return scenes


async def get_telegram_videos():
    client = Client(
        SESSION_NAME,
        api_id=API_ID,
        api_hash=API_HASH,
        workdir=str(BASE),
        no_updates=True,
        max_concurrent_transmissions=4
    )

    videos = []

    async with client as app:
        print("Connected to Telegram. Scanning channel history...")

        async for message in app.get_chat_history(CHANNEL_ID, limit=0):
            media = (
                getattr(message, "video", None)
                or getattr(message, "document", None)
            )

            if not media:
                continue

            mime = getattr(media, "mime_type", None) or ""

            if not getattr(message, "video", None):
                if not mime.startswith("video/"):
                    continue

            filename = getattr(media, "file_name", None) or ""

            videos.append({
                "message_id": int(message.id),
                "filename": filename,
                "norm_name": normalize(filename),
                "size": int(getattr(media, "file_size", 0) or 0),
                "mime": mime,
                "duration": float(getattr(media, "duration", 0) or 0),
                "width": int(getattr(media, "width", 0) or 0),
                "height": int(getattr(media, "height", 0) or 0),
                "date": str(getattr(message, "date", "") or "")
            })

            if len(videos) % 100 == 0:
                print(f"Telegram videos discovered: {len(videos)}")

    print(f"Total Telegram videos: {len(videos)}")
    return videos


def prepare_database(db):
    # Ensure all core tables exist on a fresh install while preserving existing data.
    db.execute("PRAGMA journal_mode=WAL;")

    db.execute("""
        CREATE TABLE IF NOT EXISTS media (
            message_id INTEGER PRIMARY KEY,
            filename TEXT,
            norm_name TEXT,
            size INTEGER,
            mime TEXT,
            kind TEXT,
            date TEXT
        )
    """)

    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_media_norm_name
        ON media(norm_name)
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS scene_telegram_map (
            scene_id TEXT PRIMARY KEY,
            message_id INTEGER UNIQUE NOT NULL,
            confidence TEXT
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS stash_scene_files (
            scene_id TEXT NOT NULL,
            filename TEXT NOT NULL,
            size INTEGER NOT NULL DEFAULT 0,
            duration REAL NOT NULL DEFAULT 0,
            width INTEGER NOT NULL DEFAULT 0,
            height INTEGER NOT NULL DEFAULT 0,
            title TEXT,
            last_seen TEXT NOT NULL,
            PRIMARY KEY (scene_id, filename, size)
        )
    """)


def load_database():
    with sqlite3.connect(DB_PATH) as db:
        prepare_database(db)
        db.commit()

        media = db.execute("""
            SELECT message_id, filename, norm_name, size, mime, kind, date
            FROM media
        """).fetchall()

        mappings = db.execute("""
            SELECT scene_id, message_id, confidence
            FROM scene_telegram_map
        """).fetchall()

        snapshots = db.execute("""
            SELECT scene_id, filename, size, duration,
                   width, height, title
            FROM stash_scene_files
        """).fetchall()

    known_messages = {int(row[0]) for row in media}
    mapped_scenes = {str(row[0]) for row in mappings}
    mapped_messages = {int(row[1]) for row in mappings}

    return known_messages, mapped_scenes, mapped_messages, snapshots


def build_scene_catalogue(live_scenes, snapshots):
    catalogue = defaultdict(list)

    for row in snapshots:
        scene_id, filename, size, duration, width, height, title = row

        catalogue[str(scene_id)].append({
            "scene_id": str(scene_id),
            "title": title or "",
            "filename": filename,
            "norm_name": normalize(filename),
            "size": int(size or 0),
            "duration": float(duration or 0),
            "width": int(width or 0),
            "height": int(height or 0)
        })

    # Current Stash metadata takes precedence over older snapshots.
    for scene in live_scenes:
        files = catalogue[scene["scene_id"]]
        files[:] = [
            old for old in files
            if not (
                old["filename"] == scene["filename"]
                and old["size"] == scene["size"]
            )
        ]
        files.append(scene)

    return catalogue


def score_match(scene, video):
    name_score = (
        SequenceMatcher(
            None, scene["norm_name"], video["norm_name"]
        ).ratio()
        if scene["norm_name"] and video["norm_name"]
        else 0
    )

    duration_diff = None
    if scene["duration"] and video["duration"]:
        duration_diff = abs(scene["duration"] - video["duration"])

    same_resolution = (
        scene["width"] > 0
        and scene["height"] > 0
        and scene["width"] == video["width"]
        and scene["height"] == video["height"]
    )

    exact_size = (
        scene["size"] > 0
        and scene["size"] == video["size"]
    )

    exact_name = (
        bool(scene["norm_name"])
        and scene["norm_name"] == video["norm_name"]
    )

    # Strong matches require independent metadata agreement.
    if exact_size and duration_diff is not None:
        if duration_diff <= 1 and same_resolution:
            return 100, "Exact size, duration within 1s and resolution"
        if duration_diff <= 2 and same_resolution:
            return 99, "Exact size, duration within 2s and resolution"
        if duration_diff <= 2:
            return 96, "Exact size and duration within 2s"

    if exact_name and exact_size:
        return 99, "Exact filename and exact size"

    if exact_name and duration_diff is not None:
        if duration_diff <= 2 and same_resolution:
            return 98, "Exact filename, duration and resolution"

    if duration_diff is not None and duration_diff <= 0.5:
        if name_score >= 0.95 and same_resolution:
            return 97, "Very similar filename, duration and resolution"

    if duration_diff is not None and duration_diff <= 1:
        if name_score >= 0.85 and same_resolution:
            return 94, "Similar filename, duration and resolution"

    return 0, ""


def build_candidates(catalogue, telegram, mapped_scenes, mapped_messages):
    available_scenes = {
        scene_id: files
        for scene_id, files in catalogue.items()
        if scene_id not in mapped_scenes and files
    }

    available_videos = [
        video for video in telegram
        if video["message_id"] not in mapped_messages
    ]

    scores = {}

    for scene_id, files in available_scenes.items():
        for video in available_videos:
            best_score = 0
            best_reason = ""
            best_file = None

            for scene in files:
                score, reason = score_match(scene, video)
                if score > best_score:
                    best_score = score
                    best_reason = reason
                    best_file = scene

            if best_score:
                scores[(scene_id, video["message_id"])] = (
                    best_score, best_reason, best_file, video
                )

    by_scene = defaultdict(list)
    by_message = defaultdict(list)

    for (scene_id, message_id), item in scores.items():
        by_scene[scene_id].append((message_id, item))
        by_message[message_id].append((scene_id, item))

    # Sort by score, then message ID for deterministic reports.
    for items in by_scene.values():
        items.sort(key=lambda x: (-x[1][0], x[0]))

    for items in by_message.values():
        items.sort(key=lambda x: (-x[1][0], x[0]))

    auto_pairs = set()

    # A match must be the unique best choice from both directions.
    for scene_id, scene_matches in by_scene.items():
        message_id, item = scene_matches[0]
        score = item[0]

        if score < 98:
            continue

        scene_second = (
            scene_matches[1][1][0]
            if len(scene_matches) > 1 else 0
        )

        message_matches = by_message[message_id]
        message_best_scene, message_best_item = message_matches[0]
        message_second = (
            message_matches[1][1][0]
            if len(message_matches) > 1 else 0
        )

        if message_best_scene != scene_id:
            continue

        if score - scene_second < 5:
            continue

        if score - message_second < 5:
            continue

        auto_pairs.add((scene_id, message_id))

    rows = []

    for scene_id, scene_matches in by_scene.items():
        # Keep the top 5 possible matches per scene for manual review.
        for rank, (message_id, item) in enumerate(scene_matches[:5], start=1):
            score, reason, scene, video = item
            is_auto = (scene_id, message_id) in auto_pairs

            if is_auto:
                status = "AUTO-APPLY - unique high-confidence match"
            elif rank == 1:
                status = "REVIEW - ambiguous or below auto threshold"
            else:
                status = "ALTERNATIVE - review if needed"

            rows.append({
                "scene_id": scene_id,
                "scene_filename": scene["filename"],
                "scene_size": scene["size"],
                "scene_duration": scene["duration"],
                "scene_resolution": f"{scene['width']}x{scene['height']}",
                "message_id": message_id,
                "telegram_filename": video["filename"],
                "telegram_size": video["size"],
                "telegram_duration": video["duration"],
                "telegram_resolution": f"{video['width']}x{video['height']}",
                "telegram_date": video["date"],
                "score": score,
                "reason": reason,
                "status": status
            })

    return rows, auto_pairs, len(available_scenes), len(available_videos)


def save_reports(rows, summary):
    fields = [
        "scene_id", "scene_filename", "scene_size",
        "scene_duration", "scene_resolution",
        "message_id", "telegram_filename", "telegram_size",
        "telegram_duration", "telegram_resolution",
        "telegram_date", "score", "reason", "status"
    ]

    with open(OUTPUT, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    REPORT.write_text("\n".join(summary), encoding="utf-8")


def apply_changes(live_scenes, telegram, auto_pairs, rows):
    with sqlite3.connect(DB_PATH) as db:
        db.execute("BEGIN IMMEDIATE")
        prepare_database(db)

        now = datetime.now().isoformat()

        # Save current Stash metadata for future matching,
        # including after local files are removed.
        for scene in live_scenes:
            db.execute("""
                INSERT INTO stash_scene_files
                (scene_id, filename, size, duration, width,
                 height, title, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(scene_id, filename, size) DO UPDATE SET
                    duration=excluded.duration,
                    width=excluded.width,
                    height=excluded.height,
                    title=excluded.title,
                    last_seen=excluded.last_seen
            """, (
                scene["scene_id"], scene["filename"], scene["size"],
                scene["duration"], scene["width"], scene["height"],
                scene["title"], now
            ))

        # Upsert Telegram catalogue entries.
        for video in telegram:
            db.execute("""
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
            """, (
                video["message_id"], video["filename"],
                video["norm_name"], video["size"],
                video["mime"], "video", video["date"]
            ))

        # Recheck both sides inside the transaction.
        for scene_id, message_id in sorted(auto_pairs):
            scene_exists = db.execute("""
                SELECT 1 FROM scene_telegram_map WHERE scene_id=?
            """, (scene_id,)).fetchone()

            message_exists = db.execute("""
                SELECT 1 FROM scene_telegram_map WHERE message_id=?
            """, (message_id,)).fetchone()

            if scene_exists or message_exists:
                continue

            db.execute("""
                INSERT INTO scene_telegram_map
                (scene_id, message_id, confidence)
                VALUES (?, ?, ?)
            """, (scene_id, message_id, "auto_verified_metadata"))

        db.commit()


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Save Telegram catalogue, Stash snapshots and safe matches"
    )
    args = parser.parse_args()

    if not API_ID or not API_HASH or not CHANNEL_ID:
        raise RuntimeError("Check Telegram settings in .env")

    known_messages, mapped_scenes, mapped_messages, snapshots = load_database()

    print("Fetching Stash metadata...")
    stash = get_stash_scenes()

    print("\nFetching Telegram history...")
    telegram = await get_telegram_videos()

    new_telegram = [
        video for video in telegram
        if video["message_id"] not in known_messages
    ]

    catalogue = build_scene_catalogue(stash, snapshots)

    rows, auto_pairs, available_scene_count, available_video_count = (
        build_candidates(
            catalogue, telegram, mapped_scenes, mapped_messages
        )
    )

    summary = [
        "STASH-TELEGRAM SYNCHRONIZER",
        f"Date: {datetime.now().isoformat()}",
        f"Mode: {'APPLY' if args.apply else 'DRY RUN'}",
        f"Stash scenes currently scanned: {len({s['scene_id'] for s in stash})}",
        f"Stash live file records: {len(stash)}",
        f"Saved Stash file snapshots: {len(snapshots)}",
        f"Telegram videos discovered: {len(telegram)}",
        f"New Telegram catalogue entries: {len(new_telegram)}",
        f"Existing mappings before run: {len(mapped_scenes)}",
        f"Unmapped Stash scenes considered: {available_scene_count}",
        f"Unmapped Telegram messages considered: {available_video_count}",
        f"Candidate rows in report: {len(rows)}",
        f"Unique high-confidence matches: {len(auto_pairs)}",
        f"Database changed: {'YES' if args.apply else 'NO'}",
        f"Candidate report: {OUTPUT}",
        f"Summary report: {REPORT}"
    ]

    save_reports(rows, summary)

    if args.apply:
        apply_changes(stash, telegram, auto_pairs, rows)
        summary[summary.index("Database changed: YES")] = (
            "Database changed: YES (catalogue, snapshots and safe mappings)"
        )
        REPORT.write_text("\n".join(summary), encoding="utf-8")

    print("\n" + "\n".join(summary))

    if not args.apply:
        print("\nDry run only. No database changes were made.")
        print("Inspect sync_candidates.csv before running with --apply.")


if __name__ == "__main__":
    asyncio.run(main())