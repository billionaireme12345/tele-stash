#!/usr/bin/env python3
"""Read-only audit of Stash scene images against existing Telegram mappings.

This script does NOT modify Telegram messages or the SQLite mapping database.
It discovers possible scene poster/image fields from the Stash GraphQL schema,
then prefers a production/poster image when exposed, otherwise the scene screenshot.
"""
from __future__ import annotations

import argparse
import csv
import json
import mimetypes
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BASE_DIR = Path(__file__).resolve().parent
STASH_URL = os.getenv("STASH_URL", "http://127.0.0.1:9999").rstrip("/")
STASH_API_KEY = os.getenv("STASH_API_KEY", "").strip()
DB_PATH = Path(os.getenv("DATABASE_PATH", str(BASE_DIR / "bridge.sqlite3")))


def request_json(url: str, payload: dict | None = None):
    headers = {"Accept": "application/json"}
    if STASH_API_KEY:
        headers["ApiKey"] = STASH_API_KEY
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as response:
        raw = response.read()
    return json.loads(raw.decode("utf-8"))


def graphql(query: str, variables: dict | None = None):
    result = request_json(
        STASH_URL + "/graphql",
        {"query": query, "variables": variables or {}},
    )
    if result.get("errors"):
        messages = "; ".join(e.get("message", str(e)) for e in result["errors"])
        raise RuntimeError("Stash GraphQL error: " + messages)
    return result.get("data", {})


def unwrap_type(type_obj: dict) -> dict:
    while type_obj and type_obj.get("kind") in ("NON_NULL", "LIST"):
        type_obj = type_obj.get("ofType") or {}
    return type_obj or {}


def get_type_fields(type_name: str) -> dict[str, dict]:
    q = """query($name: String!) {
      __type(name: $name) { name fields { name type { kind name ofType { kind name ofType { kind name } } } } }
    }"""
    data = graphql(q, {"name": type_name})
    typ = data.get("__type") or {}
    return {f["name"]: f["type"] for f in typ.get("fields") or []}


def get_named_type(type_obj: dict) -> str:
    return unwrap_type(type_obj).get("name", "")


def scalar_or_enum(type_obj: dict) -> bool:
    t = unwrap_type(type_obj)
    return t.get("kind") in ("SCALAR", "ENUM")


def safe_ext(url: str, content_type: str = "") -> str:
    path = urllib.parse.urlparse(url).path
    ext = Path(path).suffix.lower()
    if ext and len(ext) <= 6:
        return ext
    guessed = mimetypes.guess_extension(content_type.split(";")[0].strip()) if content_type else None
    return guessed or ".jpg"


def resolve_url(value: str) -> str:
    if value.startswith(("http://", "https://")):
        return value
    return urllib.parse.urljoin(STASH_URL + "/", value.lstrip("/"))


def download_image(value: str, destination: Path) -> tuple[bool, str]:
    url = resolve_url(value)
    headers = {"Accept": "image/*,*/*;q=0.8"}
    if STASH_API_KEY:
        headers["ApiKey"] = STASH_API_KEY
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=90) as response:
            content_type = response.headers.get("Content-Type", "")
            data = response.read()
        if not data:
            return False, "empty image response"
        if content_type and not content_type.lower().startswith("image/"):
            return False, f"not an image ({content_type})"
        destination = destination.with_suffix(safe_ext(url, content_type))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        return True, str(destination)
    except Exception as exc:
        return False, str(exc)


def load_mappings(db_path: Path) -> dict[str, int]:
    if not db_path.exists():
        raise FileNotFoundError(f"Mapping database not found: {db_path}")
    with sqlite3.connect(db_path) as db:
        rows = db.execute(
            "SELECT scene_id, message_id FROM scene_telegram_map ORDER BY CAST(scene_id AS INTEGER)"
        ).fetchall()
    return {str(scene_id): int(message_id) for scene_id, message_id in rows}


def build_scene_query(scene_fields: dict[str, dict], paths_type: str | None):
    # Candidate fields that may represent a scene-specific production poster.
    candidate_names = ("poster", "cover", "image", "poster_image", "cover_image")
    scalar_candidates = [n for n in candidate_names if n in scene_fields and scalar_or_enum(scene_fields[n])]
    path_selection = ""
    if "paths" in scene_fields and paths_type:
        path_fields = get_type_fields(paths_type)
        wanted = [n for n in ("screenshot", "preview") if n in path_fields and scalar_or_enum(path_fields[n])]
        if wanted:
            path_selection = "paths { " + " ".join(wanted) + " }"
    selected = ["id"]
    for simple in ("title",):
        if simple in scene_fields and scalar_or_enum(scene_fields[simple]):
            selected.append(simple)
    if "files" in scene_fields:
        file_type = get_named_type(scene_fields["files"])
        file_fields = get_type_fields(file_type) if file_type else {}
        file_wanted = [n for n in ("basename", "path", "size", "duration")
                       if n in file_fields and scalar_or_enum(file_fields[n])]
        if file_wanted:
            selected.append("files { " + " ".join(file_wanted) + " }")
    selected.append(path_selection)
    selected.extend(scalar_candidates)
    selected = [s for s in selected if s]
    query = """query($filter: FindFilterType) {
      findScenes(filter: $filter) {
        count
        scenes { %s }
      }
    }""" % " ".join(selected)
    return query, scalar_candidates, path_selection


def main():
    parser = argparse.ArgumentParser(description="Audit Stash posters/screenshots for mapped Telegram videos")
    parser.add_argument("--db", default=str(DB_PATH), help="Path to bridge.sqlite3")
    parser.add_argument("--out", default="stash_cover_audit.csv", help="CSV audit report")
    parser.add_argument("--covers", default="covers", help="Directory for downloaded image previews")
    parser.add_argument("--limit", type=int, default=100, help="Scenes per Stash page")
    args = parser.parse_args()

    db_path = Path(args.db).expanduser().resolve()
    out_csv = Path(args.out).expanduser().resolve()
    covers_dir = Path(args.covers).expanduser().resolve()

    print(f"Stash: {STASH_URL}")
    print(f"Database: {db_path}")
    mappings = load_mappings(db_path)
    print(f"Existing scene-to-Telegram mappings: {len(mappings)}")

    scene_fields = get_type_fields("Scene")
    if not scene_fields:
        raise RuntimeError("Could not inspect Stash GraphQL Scene type. Check Stash URL/API access.")
    paths_type = get_named_type(scene_fields.get("paths", {})) if "paths" in scene_fields else None
    query, poster_fields, path_selection = build_scene_query(scene_fields, paths_type)
    print("Possible production image fields exposed by this Stash version:", poster_fields or "none")
    if paths_type:
        print("Scene paths type:", paths_type)

    rows = []
    page = 1
    while True:
        data = graphql(query, {"filter": {"page": page, "per_page": max(1, min(args.limit, 1000))}})
        result = data.get("findScenes") or {}
        scenes = result.get("scenes") or []
        if not scenes:
            break
        for scene in scenes:
            scene_id = str(scene.get("id", ""))
            if scene_id not in mappings:
                continue
            message_id = mappings[scene_id]
            selected_value = ""
            selected_source = ""
            # Prefer production-style scalar fields if the schema exposes them.
            for field in poster_fields:
                value = scene.get(field)
                if isinstance(value, str) and value.strip():
                    selected_value, selected_source = value, field
                    break
            # Fallback to Stash's generated scene screenshot.
            if not selected_value:
                paths = scene.get("paths") or {}
                screenshot = paths.get("screenshot") if isinstance(paths, dict) else None
                if screenshot:
                    selected_value, selected_source = screenshot, "screenshot"
            # Preview is a last-resort thumbnail fallback if screenshot is absent.
            if not selected_value:
                paths = scene.get("paths") or {}
                preview = paths.get("preview") if isinstance(paths, dict) else None
                if preview:
                    selected_value, selected_source = preview, "preview"

            filename = ""
            status = "NO_IMAGE"
            error = ""
            if selected_value:
                target = covers_dir / f"scene_{scene_id}_{selected_source}"
                ok, result_path = download_image(selected_value, target)
                if ok:
                    filename = result_path
                    status = "IMAGE_DOWNLOADED"
                else:
                    status = "IMAGE_DOWNLOAD_FAILED"
                    error = result_path

            files = scene.get("files") or []
            rows.append({
                "scene_id": scene_id,
                "scene_title": scene.get("title") or "",
                "telegram_message_id": message_id,
                "cover_source": selected_source,
                "cover_url": selected_value,
                "downloaded_image": filename,
                "scene_file_count": len(files),
                "status": status,
                "error": error,
            })
            print(f"Scene {scene_id} -> Telegram {message_id}: {status} ({selected_source or 'none'})")
        if len(scenes) < max(1, min(args.limit, 1000)):
            break
        page += 1

    fieldnames = ["scene_id", "scene_title", "telegram_message_id", "cover_source", "cover_url", "downloaded_image", "scene_file_count", "status", "error"]
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    counts = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    print("\nREAD-ONLY AUDIT COMPLETE")
    print(f"Mapped scenes audited: {len(rows)}")
    for status, count in sorted(counts.items()):
        print(f"{status}: {count}")
    print(f"CSV report: {out_csv}")
    print(f"Downloaded previews: {covers_dir}")
    print("No Telegram messages or SQLite mappings were modified.")


if __name__ == "__main__":
    try:
        main()
    except (Exception, KeyboardInterrupt) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
