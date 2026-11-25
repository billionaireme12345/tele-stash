# Tele-Stash (WZGram Edition)

A **local-first StashApp-to-Telegram streaming bridge** for Windows powered by **WZGram (MTProto)**. Keep your full StashApp library—scenes, metadata, tags, performers, markers, and thumbnails—intact while offloading large video files to a private Telegram channel to save local disk space.

When a video exists on your hard drive, Stash plays it locally at full disk speed. When you delete the local file, Tele-Stash seamlessly streams it on demand from Telegram with full seeking, RAM caching, and zero permanent local video storage.

---

## Table of Contents

1. [Features](#features)
2. [Architecture](#architecture)
3. [Prerequisites](#prerequisites)
4. [Project Structure](#project-structure)
5. [Fresh Installation Guide](#fresh-installation-guide)
6. [Daily Usage & Batch Controls](#daily-usage--batch-controls)
7. [Mapping & Cover Sync Workflows](#mapping--cover-sync-workflows)
8. [Configuration Reference (`.env`)](#configuration-reference-env)
9. [Endpoints & Database Schema](#endpoints--database-schema)
10. [Important Warnings & Troubleshooting](#important-warnings--troubleshooting)
11. [Security & Privacy](#security--privacy)

---

## Features

### Local-First Playback with Automatic Telegram Fallback
* **Local Priority:** Always tries Stash's native local file stream first (`http://127.0.0.1:9999/scene/{id}/stream`).
* **Seamless Cloud Fallback:** If the local file is missing or deleted (`404`/`410`/`500`/`502`), the proxy automatically looks up the scene's Telegram message ID in SQLite and streams it from Telegram.
* **Automatic Recovery:** If you ever re-download or restore a local file to its original path, playback automatically switches back to local storage on the next request.
* **Non-Destructive:** Never modifies your Stash database, scene IDs, file paths, or metadata.

### High-Speed WZGram Streaming Engine (`bridge_wzgram_v2.py`)
* **MTProto Chunk Streaming:** Uses `wzgram` (`1 MiB` aligned chunks) rather than the Bot API (avoiding the 20 MB bot download limit) and significantly outperforms standard Telethon downloads.
* **256 MiB In-Memory LRU RAM Cache:** Recently played and revisited ranges are served from RAM with zero disk writes and automatic eviction.
* **Connection Stability:** Uses a serialized download semaphore (`asyncio.Semaphore(1)`) around Telegram fetches while releasing locks before HTTP delivery, preventing Telegram server disconnects during rapid seeking.
* **Unicode Filename Support:** Encodes non-ASCII filenames using RFC 5987 (`filename*=UTF-8''...`) with safe ASCII fallbacks so special characters (such as curly apostrophes) never trigger HTTP header errors.
* **Zero Permanent Storage:** Video data is streamed directly into memory buffers and discarded after eviction.

### Same-Origin Reverse Proxy & Smooth Seeking (`proxy.py` + Stash Plugin)
* **CSP & WebSocket Compatibility:** Serves both Stash and Telegram streams from a single origin (`http://127.0.0.1:9998`), satisfying Stash's `Content-Security-Policy` (`media-src blob: 'self'`) and proxying `graphql-transport-ws` WebSockets.
* **Accurate Seeking:** Supports standard HTTP `206 Partial Content` byte-range requests as well as FFmpeg-powered fragmented MP4 timestamp seeking (`?start=seconds`).
* **Video.js Offset Fix:** The included Stash UI plugin (`telegram_stream_override.js`) dynamically routes every scene through `/smart-stream/{scene_id}` and intercepts Video.js source initialization (`offset: false`) to prevent double-seeking timeline drift.
* **Anti-Stale Caching:** Injects `Cache-Control: no-store, no-cache, must-revalidate, max-age=0` headers on video streams so replacing or remuxing a local video never serves a broken Chrome disk-cache response.

### Automated Scene Mapping & Cover Synchronization
* **Multi-Factor Auto-Matcher (`sync_stash_telegram.py`):** Matches Stash scenes to Telegram uploads using normalized filename, exact file size, duration, and resolution—safely disambiguating duplicate filenames (e.g., multiple `vid_720p.mp4` files) and extension changes (`.mkv`/`.ts` uploaded as `.mp4`).
* **Dry-Run First & One-to-One Protection:** Previews all proposed matches in `sync_candidates.csv` before touching SQLite, never overwrites existing or manually verified mappings, and preserves historical Stash file metadata snapshots (`stash_scene_files`).
* **Manual Mapping Controls:** Interactive scripts to link (`03_Manual_Map.bat`) or unlink (`04_Remove_Mapping.bat`) individual scenes by Stash Scene ID and Telegram Message ID.
* **Telegram Video Cover Sync:** Extracts Stash scene posters/screenshots (`stash_cover_audit.py`) and updates already-uploaded Telegram videos' cover images via MTProto (`bulk_update_telegram_covers.py`) **without re-uploading the video files**, complete with original thumbnail backups and resumable CSV logs.

---

## Architecture

```text
Browser (Open Stash at http://127.0.0.1:9998)
   │
   ▼
Stash UI Plugin (telegram_stream_override.js)
   │  Routes scene playback -> /smart-stream/{scene_id}
   │  Forces Video.js offset = false for accurate timeline seeking
   ▼
Reverse Proxy — proxy.py (127.0.0.1:9998)
   ├── All normal UI / GraphQL / WebSocket requests ──► StashApp (127.0.0.1:9999)
   └── /smart-stream/{scene_id}
          ├── 1. Try local video ─────────────────────► StashApp (127.0.0.1:9999)
          └── 2. If missing (404/410/500/502)
                 └─► Lookup scene_id in bridge.sqlite3
                 └─► Fallback stream ─────────────────► WZGram Bridge (127.0.0.1:8767)
                                                           ├── 256 MiB RAM LRU Cache
                                                           ├── FFmpeg Timestamp Seek (?start=)
                                                           └── Private Telegram Channel (MTProto)
```

---

## Prerequisites

Before installing on a fresh Windows PC, make sure you have:

1. **Windows 10 / 11**
2. **Python 3.10 or 3.11** (tested on Python 3.11) with the `py` launcher installed
3. **FFmpeg & FFprobe** installed and added to your Windows `PATH` (required for timestamp seeking and container inspection)
4. **StashApp** (tested on `v0.32.0`) running locally on port `9999`
5. **Telegram API Credentials:** Obtain your personal `api_id` and `api_hash` from [https://my.telegram.org](https://my.telegram.org) under **API development tools**
6. **Private Telegram Channel ID:** Your channel's numeric ID (typically prefixed with `-100`, e.g., `-1001234567890`)

---

## Project Structure

```text
tele-stash/
├── .env.example                           # Environment variable template
├── .gitignore                             # Blocks secrets, sessions, DBs, logs, and venvs
├── README.md                              # Project documentation
├── requirements.txt                       # Python dependencies (wzgram, fastapi, uvicorn, aiohttp, dotenv)
│
├── bridge_wzgram_v2.py                    # Core WZGram streaming server (port 8767)
├── proxy.py                               # Local-first reverse proxy + CSP/WebSocket handler (port 9998)
├── sync_stash_telegram.py                 # Automated Stash-to-Telegram scanner & metadata matcher
├── manual_map_telegram.py                 # Interactive single-scene manual mapper
├── stash_cover_audit.py                   # Extracts Stash scene posters/screenshots into covers/
├── bulk_update_telegram_covers.py         # Updates existing Telegram video covers via MTProto
├── export_telegram_metadata.py            # Utility: exports Telegram video metadata to CSV
├── import_mapping.py                      # Utility: bulk-imports verified mappings from CSV
│
├── 00_Install_Setup.bat                   # One-click fresh environment + plugin installer
├── Start_All.bat                          # Starts Stash, WZGram Bridge (min), and Proxy (min)
├── Stop_All.bat                           # Cleanly stops Stash, Bridge, Proxy, and child processes
├── Check_Status.bat                       # Checks ports 9999/8767/9998 and /health endpoint
├── 01_Scan_Review.bat                     # Dry-run scan & review of new matches
├── 02_Apply_High_Confidence.bat           # Applies unique high-confidence matches to SQLite
├── 03_Manual_Map.bat                      # Manually links a Stash Scene ID to a Telegram Message ID
├── 04_Remove_Mapping.bat                  # Removes an incorrect mapping row from SQLite
├── 05_run_stash_cover_audit.bat           # Runs Stash poster/thumbnail extraction audit
├── 06_run_bulk_update_telegram_covers.bat # Runs Telegram video cover updater
│
└── stash_plugin/
    ├── telegram_stream_override.js        # Stash UI plugin (dynamic scene routing + seek fix)
    └── telegram_stream_override.yml       # Stash plugin manifest
```

---

## Fresh Installation Guide

### Step 1: Download & Extract
Clone the repository or extract the project folder to a permanent location on your PC (e.g., `C:\Tools\tele-stash\`).

### Step 2: Run the Automated Installer
Double-click **`00_Install_Setup.bat`**. This script automatically:
1. Checks that Python and FFmpeg/FFprobe are available.
2. Creates a dedicated virtual environment (`venv-wzgram`) and installs all packages from `requirements.txt`.
3. Copies `.env.example` to `.env` if it doesn't already exist.
4. Copies the Stash UI plugin (`telegram_stream_override.js` and `.yml`) into `%USERPROFILE%\.stash\plugins\`.

### Step 3: Configure `.env`
Open the newly created `.env` file in Notepad and fill in your Telegram credentials and channel ID:

```ini
TG_API_ID=
TG_API_HASH=
TG_PHONE=+91
TG_CHANNEL_ID=
HOST=127.0.0.1
PORT=8767
```

### Step 4: Configure StashApp
1. Start Stash (`stash-win.exe`).
2. Open Stash in your browser, go to **Settings → Plugins**, click **Reload plugins**, and verify **Telegram Stream Override** is enabled.
3. Hard-refresh your browser (`Ctrl + F5`).
4. *(Recommended)* If you only want Stash to scan videos and ignore image files, go to **Settings → Library**, toggle **Images** off on each library folder, and disable **Create galleries from folders containing images**.
5. *(Optional)* If `stash-win.exe` is not in the `tele-stash` folder or your Windows `PATH`, edit `STASH_EXE` at the top of `Start_All.bat` to point to its full path (e.g., `C:\Stash\stash-win.exe`).

### Step 5: First-Time Telegram Login & Initial Scene Sync
Make sure **Stash is running on port `9999`** and the WZGram bridge is **stopped** (so only one process accesses the Telegram session file).

1. Double-click **`01_Scan_Review.bat`**.
2. On the very first run, `sync_stash_telegram.py` automatically creates `bridge.sqlite3` and its tables, and WZGram will prompt you in the terminal for your **Telegram phone number**, **login OTP**, and (if enabled) **2FA password**. This creates your local `.session` file so future runs won't ask again.
3. Once scanning finishes, inspect `sync_candidates.csv` and `sync_summary.txt` to review the discovered matches.
4. Double-click **`02_Apply_High_Confidence.bat`** and confirm to save the Telegram catalog, Stash file snapshots, and verified scene mappings into `bridge.sqlite3`.

### Step 6: Launch and Watch
1. Double-click **`Start_All.bat`**. It starts Stash (`--nobrowser`), WZGram Bridge (`:8767`, minimized), and the Reverse Proxy (`:9998`, minimized), then closes itself automatically.
2. *(Optional)* Run **`Check_Status.bat`** to confirm all three ports (`9999`, `8767`, `9998`) are listening and `/health` returns `"telegram_connected": true`.
3. Open your browser and bookmark:
   ```text
   http://127.0.0.1:9998
   ```
   > **Always access Stash through port `9998`** (the reverse proxy), not `9999`, so Telegram streams work seamlessly under the same browser origin.

---

## Daily Usage & Batch Controls

| Batch File | Purpose | Notes |
| :--- | :--- | :--- |
| **`00_Install_Setup.bat`** | First-time setup (venv, pip packages, `.env`, Stash plugin) | Run once on a fresh PC |
| **`Start_All.bat`** | Starts Stash (`9999`), WZGram Bridge (`8767`), and Proxy (`9998`) | Minimizes service terminals & auto-closes |
| **`Stop_All.bat`** | Stops Stash, WZGram Bridge, Proxy, and active FFmpeg subprocesses | Gracefully closes Stash first to protect DB |
| **`Check_Status.bat`** | Displays listening status for ports `9999`, `8767`, `9998` & bridge health | Quick diagnostics |
| **`01_Scan_Review.bat`** | Dry-run sync of Stash scenes and Telegram channel history | Read-only; outputs `sync_candidates.csv` |
| **`02_Apply_High_Confidence.bat`** | Writes catalog, snapshots, and unique high-confidence matches to DB | Safe; never overwrites existing mappings |
| **`03_Manual_Map.bat`** | Manually links a Stash Scene ID to a Telegram Message ID | Saves as `manual_verified` |
| **`04_Remove_Mapping.bat`** | Deletes a single incorrect row from `scene_telegram_map` | Leaves Stash scene & Telegram video untouched |
| **`05_run_stash_cover_audit.bat`** | Downloads Stash scene posters/screenshots into `covers/` | Read-only against Telegram |
| **`06_run_bulk_update_telegram_covers.bat`** | Applies extracted covers to mapped Telegram videos via MTProto | Stop WZGram Bridge first |

---

## Mapping & Cover Sync Workflows

### Adding New Videos Later
1. Download your videos locally, place them in your Stash library folder, and click **Scan** in Stash so scenes and metadata are created.
2. Upload the videos to your private Telegram channel (in batches or one by one).
3. Run **`Stop_All.bat`** (or stop the WZGram bridge window) so the Telegram session file isn't locked, keep Stash running, then run **`01_Scan_Review.bat`** followed by **`02_Apply_High_Confidence.bat`**.
4. Once mapped and verified, you can safely delete the local video files to free up storage—Stash will automatically stream them from Telegram.

### Manually Mapping Unmatched Videos
If a video was heavily re-encoded or renamed on upload and didn't auto-match:
1. In Telegram Desktop, right-click the video → **Copy Message Link** (e.g., `https://t.me/c/4464532881/520` → Message ID is **`520`**).
2. Open the scene in Stash and note its numeric ID from the URL (e.g., `.../scenes/400` → Scene ID is **`400`**).
3. Run **`03_Manual_Map.bat`**, enter both IDs, verify the displayed Telegram metadata, and press `y`.

### Syncing Stash Posters to Telegram Video Covers
1. Run **`05_run_stash_cover_audit.bat`** while Stash is running to extract posters into `covers/` and generate `stash_cover_audit.csv`.
2. Stop the WZGram bridge (`8767`) so the Telegram session is free.
3. Run **`06_run_bulk_update_telegram_covers.bat`** (dry-run first to inspect, then `--apply` to update covers). Original Telegram thumbnails are backed up to `cover_backups/` and progress is tracked in `telegram_cover_update_report.csv`.

---

## Configuration Reference (`.env`)

| Variable | Default / Example | Description |
| :--- | :--- | :--- |
| `TG_API_ID` | `12345678` | Numeric Telegram API ID from `my.telegram.org` |
| `TG_API_HASH` | `abc123...` | Telegram API hash from `my.telegram.org` |
| `TG_CHANNEL_ID` | `-1001234567890` | Numeric ID of your private Telegram video channel |
| `WZGRAM_SESSION` | `wzgram_benchmark` | Filename prefix for the local WZGram `.session` file |
| `FFMPEG_BIN` | `ffmpeg` | Path to FFmpeg executable (or `ffmpeg` if in Windows `PATH`) |
| `WZGRAM_BRIDGE_BASE_URL` | `http://127.0.0.1:8767` | Internal base URL used by FFmpeg during timestamp seeking |
| `STASH_URL` | `http://127.0.0.1:9999/graphql` | Local Stash GraphQL endpoint for sync and audit scripts |
| `STASH_API_KEY` | *(optional)* | Stash API key, only needed if authentication is enabled in Stash |

---

## Endpoints & Database Schema

### HTTP Endpoints
* **`GET http://127.0.0.1:8767/health`** — Returns bridge connection state, chunk size (`1 MiB`), RAM cache usage/limit (`256 MiB`), and session name.
* **`GET|HEAD http://127.0.0.1:8767/stream/{message_id}`** — Direct WZGram HTTP Range stream (`206 Partial Content`) with optional `?start=<seconds>` FFmpeg timestamp seek.
* **`GET|HEAD http://127.0.0.1:9998/smart-stream/{scene_id}`** — Local-first proxy endpoint: tries Stash's local file first and automatically falls back to WZGram if unavailable.
* **`GET|HEAD http://127.0.0.1:9998/telegram-stream/{message_id}`** — Same-origin proxied Telegram stream.

### SQLite Tables (`bridge.sqlite3`)
Automatically initialized in WAL mode on first run:
* **`media`** — Indexed Telegram video catalog (`message_id`, `filename`, `norm_name`, `size`, `mime`, `kind`, `date`).
* **`scene_telegram_map`** — One-to-one mapping between Stash `scene_id` and Telegram `message_id` (`confidence`: `auto_verified_metadata` or `manual_verified`).
* **`stash_scene_files`** — Snapshots of Stash scene file metadata (`scene_id`, `filename`, `size`, `duration`, `width`, `height`, `title`, `last_seen`) to retain matching metadata even after local files are deleted.

---

## Important Warnings & Troubleshooting

* **NEVER run `Settings → Tasks → Clean` in Stash:** Because your local video files are deleted after uploading to Telegram, running Stash's Clean task will permanently remove those scenes from your Stash database.
* **`sqlite3.OperationalError: database is locked` on `.session`:** Only one WZGram process should use `wzgram_benchmark.session` at a time. Stop the WZGram bridge (`8767`) before running `01_Scan_Review.bat`, `02_Apply_High_Confidence.bat`, `03_Manual_Map.bat`, or `06_run_bulk_update_telegram_covers.bat`.
* **Video fails with `MEDIA_ERR_SRC_NOT_SUPPORTED` / `DEMUXER_ERROR_COULD_NOT_OPEN`:** Some downloaded `.mp4` files are actually MPEG-TS containers (`format_name: mpegts`) with an `.mp4` extension. Check with `ffprobe` and losslessly remux without re-encoding:
  ```powershell
  ffmpeg -i "input.mp4" -map 0:v:0 -map 0:a? -c copy -movflags +faststart "output_fixed.mp4"
  ```
* **Media blocked by Content Security Policy (CSP):** Make sure you opened Stash at **`http://127.0.0.1:9998`**, not `http://localhost:9999`.
* **Plugin not loading after updates:** Go to **Settings → Plugins → Reload plugins** in Stash and press **`Ctrl + F5`** in your browser.

---

## Security & Privacy

* All services bind strictly to **`127.0.0.1`** (local machine only)—no ports are exposed publicly.
* **Never commit or share:** `.env`, `*.session`, `*.session-shm`, `*.session-wal`, `bridge.sqlite3`, `covers/`, `cover_backups/`, `*.csv`, or `*.log`. Verify with `git status` before pushing to GitHub.
