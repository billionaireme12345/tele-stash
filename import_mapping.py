import csv
import sqlite3
from pathlib import Path

BASE = Path(__file__).resolve().parent
CSV_FILE = BASE / "stash_telegram_verified_mapping_enriched.csv"
DB_FILE = BASE / "bridge.sqlite3"

if not CSV_FILE.exists():
    raise FileNotFoundError(CSV_FILE)

with CSV_FILE.open("r", encoding="utf-8-sig", newline="") as f:
    reader = csv.DictReader(f)

    required = {"scene_id", "message_id", "confidence"}
    if not reader.fieldnames or not required.issubset(reader.fieldnames):
        raise ValueError(f"Unexpected CSV columns: {reader.fieldnames}")

    rows = []
    seen = set()

    for row in reader:
        scene_id = str(row["scene_id"]).strip()
        message_id = int(row["message_id"])
        confidence = str(row["confidence"]).strip()

        if not scene_id or message_id <= 0:
            raise ValueError(f"Invalid mapping: {row}")

        if scene_id in seen:
            raise ValueError(f"Duplicate scene ID: {scene_id}")

        seen.add(scene_id)
        rows.append((scene_id, message_id, confidence))

if not rows:
    raise RuntimeError("No mappings found in CSV.")

with sqlite3.connect(DB_FILE) as db:
    db.execute("""
        CREATE TABLE IF NOT EXISTS scene_telegram_map (
            scene_id TEXT PRIMARY KEY,
            message_id INTEGER NOT NULL,
            confidence TEXT NOT NULL
        )
    """)

    db.executemany("""
        INSERT INTO scene_telegram_map
            (scene_id, message_id, confidence)
        VALUES (?, ?, ?)
        ON CONFLICT(scene_id) DO UPDATE SET
            message_id = excluded.message_id,
            confidence = excluded.confidence
    """, rows)

    count = db.execute(
        "SELECT COUNT(*) FROM scene_telegram_map"
    ).fetchone()[0]

    test = db.execute("""
        SELECT scene_id, message_id, confidence
        FROM scene_telegram_map
        WHERE scene_id = '105'
    """).fetchone()

print("========== IMPORT COMPLETE ==========")
print("Imported mappings:", len(rows))
print("Total mappings in table:", count)
print("Scene 105:", test)
print("======================================")
