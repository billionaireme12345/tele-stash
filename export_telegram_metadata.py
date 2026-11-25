import asyncio
import csv
import os
import sqlite3
import time
from pathlib import Path
from dotenv import load_dotenv
from wzgram import Client

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / ".env")

DB_PATH = BASE / "bridge.sqlite3"
OUTPUT = BASE / "telegram_video_metadata.csv"
BATCH_SIZE = 50

async def main():
    channel_id = int(os.getenv("TG_CHANNEL_ID", "0"))

    if not channel_id:
        raise RuntimeError("TG_CHANNEL_ID missing in .env")

    db = sqlite3.connect(DB_PATH)
    message_ids = [
        row[0] for row in db.execute(
            "SELECT message_id FROM media WHERE kind='video' ORDER BY message_id"
        ).fetchall()
    ]
    db.close()

    print(f"Videos in database: {len(message_ids)}")

    client = Client(
        "wzgram_benchmark",
        api_id=int(os.getenv("TG_API_ID", "0")),
        api_hash=os.getenv("TG_API_HASH", ""),
        workdir=str(BASE),
        no_updates=True,
        max_concurrent_transmissions=4
    )

    rows = []
    found = 0
    missing = 0

    async with client as app:
        print("Connected. Fetching Telegram metadata...\n")

        for start in range(0, len(message_ids), BATCH_SIZE):
            batch = message_ids[start:start + BATCH_SIZE]

            messages = await app.get_messages(
                channel_id,
                message_ids=batch
            )

            if not isinstance(messages, (list, tuple)):
                messages = [messages]

            for message in messages:
                if not message:
                    continue

                media = (
                    getattr(message, "video", None)
                    or getattr(message, "document", None)
                )

                if not media:
                    continue

                rows.append({
                    "message_id": message.id,
                    "filename": getattr(media, "file_name", None),
                    "file_size": getattr(media, "file_size", None),
                    "mime_type": getattr(media, "mime_type", None),
                    "duration": getattr(media, "duration", None),
                    "width": getattr(media, "width", None),
                    "height": getattr(media, "height", None),
                    "date": getattr(message, "date", None),
                    "media_type": "video" if getattr(message, "video", None) else "document"
                })
                found += 1

            missing += len(batch) - sum(
                1 for m in messages if m is not None
            )

            print(
                f"Processed {min(start + BATCH_SIZE, len(message_ids))}"
                f"/{len(message_ids)} | Metadata found: {found}"
            )

            await asyncio.sleep(0.3)

    fields = [
        "message_id", "filename", "file_size", "mime_type",
        "duration", "width", "height", "date", "media_type"
    ]

    with open(OUTPUT, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    print("\n========== EXPORT COMPLETE ==========")
    print(f"Database messages: {len(message_ids)}")
    print(f"Metadata retrieved: {found}")
    print(f"Missing/unavailable: {missing}")
    print(f"CSV: {OUTPUT}")
    print("=====================================")

if __name__ == "__main__":
    asyncio.run(main())
