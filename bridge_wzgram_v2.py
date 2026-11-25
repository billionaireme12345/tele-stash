import asyncio
import mimetypes
import os
import re
import sqlite3
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from wzgram import Client


def make_content_disposition(filename):
    """Build a Latin-1-safe header while preserving the UTF-8 filename."""
    filename = Path(filename).name
    ascii_name = filename.encode("ascii", "ignore").decode("ascii")
    ascii_name = ascii_name.replace('"', "").replace("\\", "")
    if not ascii_name:
        ascii_name = "video"
    encoded_name = quote(filename, safe="")
    return (
        f'inline; filename="{ascii_name}"; '
        f"filename*=UTF-8''{encoded_name}"
    )


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

API_ID = int(os.getenv("TG_API_ID", "0"))
API_HASH = os.getenv("TG_API_HASH", "")
CHANNEL_ID = int(os.getenv("TG_CHANNEL_ID", "0"))
SESSION_NAME = os.getenv("WZGRAM_SESSION", "wzgram_benchmark")
DB_PATH = BASE_DIR / "bridge.sqlite3"
FFMPEG_BIN = os.getenv("FFMPEG_BIN", "ffmpeg")
BRIDGE_BASE_URL = os.getenv("WZGRAM_BRIDGE_BASE_URL", "http://127.0.0.1:8767")

# wzgram stream_media uses 1 MiB units for offset/limit.
CHUNK_SIZE = 1024 * 1024
CACHE_LIMIT = 256 * 1024 * 1024
PREFETCH_CHUNKS = 0
MAX_RETRIES = 3

db = sqlite3.connect(DB_PATH, check_same_thread=False)


def init_db():
    """Ensure required SQLite tables exist on a fresh install."""
    db.execute("PRAGMA journal_mode=WAL;")
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS media (
            message_id INTEGER PRIMARY KEY,
            filename TEXT,
            norm_name TEXT,
            size INTEGER,
            mime TEXT,
            kind TEXT,
            date TEXT
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_media_norm_name ON media(norm_name)"
    )
    db.commit()


init_db()

client = Client(
    SESSION_NAME,
    api_id=API_ID,
    api_hash=API_HASH,
    workdir=str(BASE_DIR),
    no_updates=True,
    max_concurrent_transmissions=2,
)

cache = OrderedDict()
cache_bytes = 0
cache_lock = asyncio.Lock()
prefetch_tasks = {}
prefetch_sem = asyncio.Semaphore(1)
telegram_stream_sem = asyncio.Semaphore(1)
connected = False


async def cache_get(key):
    async with cache_lock:
        data = cache.get(key)
        if data is not None:
            cache.move_to_end(key)
        return data


async def cache_put(key, data):
    global cache_bytes
    if not data or len(data) > CACHE_LIMIT:
        return
    async with cache_lock:
        old = cache.pop(key, None)
        if old is not None:
            cache_bytes -= len(old)
        cache[key] = data
        cache_bytes += len(data)
        while cache_bytes > CACHE_LIMIT and cache:
            _, old_data = cache.popitem(last=False)
            cache_bytes -= len(old_data)


def catalog_row(message_id):
    return db.execute(
        "SELECT filename,size,mime FROM media WHERE message_id=?",
        (message_id,),
    ).fetchone()


async def get_telegram_message(message_id):
    if not CHANNEL_ID:
        raise HTTPException(500, "TG_CHANNEL_ID is not configured")
    try:
        message = await client.get_messages(CHANNEL_ID, message_id)
    except Exception as exc:
        raise HTTPException(502, f"Telegram message lookup failed: {exc}") from exc
    if not message:
        raise HTTPException(404, "Telegram message not found")
    media = (
        getattr(message, "video", None)
        or getattr(message, "document", None)
        or getattr(message, "audio", None)
        or getattr(message, "animation", None)
    )
    if media is None:
        raise HTTPException(404, "Telegram message has no downloadable media")
    return message


def parse_range(range_header, size):
    start, end, status = 0, size - 1, 200
    if not range_header:
        return start, end, status
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
    if not match:
        raise HTTPException(416, "Only one byte range is supported")
    first, last = match.groups()
    if first:
        start = int(first)
        end = int(last) if last else size - 1
    elif last:
        suffix = int(last)
        if suffix <= 0:
            raise HTTPException(416, "Invalid suffix byte range")
        start = max(0, size - suffix)
        end = size - 1
    if start >= size or end < start:
        raise HTTPException(416, headers={"Content-Range": f"bytes */{size}"})
    return start, min(end, size - 1), 206


async def prefetch(message_id, message, size, start_index):
    async with prefetch_sem:
        try:
            index = start_index
            end_index = min(
                start_index + PREFETCH_CHUNKS,
                (size + CHUNK_SIZE - 1) // CHUNK_SIZE,
            )
            while index < end_index:
                if await cache_get((message_id, index)) is not None:
                    index += 1
                    continue
                # Fetch a contiguous missing run in one wzgram stream_media call.
                run_start = index
                while index < end_index and await cache_get((message_id, index)) is None:
                    index += 1
                remaining = index - run_start
                current = run_start
                retries = 0
                while current < run_start + remaining:
                    try:
                        progressed = False
                        async for part in client.stream_media(
                            message,
                            offset=current,
                            limit=run_start + remaining - current,
                        ):
                            data = bytes(part)
                            if not data:
                                break
                            await cache_put((message_id, current), data)
                            current += 1
                            progressed = True
                        if current >= run_start + remaining:
                            break
                        if not progressed:
                            raise RuntimeError("wzgram returned no data during prefetch")
                    except Exception:
                        retries += 1
                        if retries >= MAX_RETRIES:
                            break
                        await asyncio.sleep(0.4 * retries)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[wzgram prefetch {message_id}] {type(exc).__name__}: {exc}")
        finally:
            prefetch_tasks.pop(message_id, None)


def schedule_prefetch(message_id, message, size, start_index):
    old_task = prefetch_tasks.get(message_id)
    if old_task is None or old_task.done():
        prefetch_tasks[message_id] = asyncio.create_task(
            prefetch(message_id, message, size, start_index)
        )


@asynccontextmanager
async def lifespan(app):
    global connected
    if not API_ID or not API_HASH:
        raise RuntimeError("Set TG_API_ID and TG_API_HASH in .env")
    init_db()
    await client.start()
    connected = True
    try:
        yield
    finally:
        connected = False
        for task in list(prefetch_tasks.values()):
            task.cancel()
        if prefetch_tasks:
            await asyncio.gather(*prefetch_tasks.values(), return_exceptions=True)
        await client.stop()
        db.close()


app = FastAPI(title="Stash wzgram Telegram Bridge v2", lifespan=lifespan)


@app.get("/health")
async def health():
    return {
        "ok": connected,
        "telegram_connected": connected,
        "library": "wzgram",
        "chunk_size": CHUNK_SIZE,
        "cache_bytes": cache_bytes,
        "cache_limit_bytes": CACHE_LIMIT,
        "prefetch_chunks": PREFETCH_CHUNKS,
        "max_concurrent_transmissions": 2,
        "session": SESSION_NAME,
    }


@app.api_route("/stream/{message_id}", methods=["GET", "HEAD"])
async def stream(message_id: int, request: Request):
    row = catalog_row(message_id)
    if not row:
        raise HTTPException(
            404, "Message not indexed; run sync_stash_telegram.py first"
        )
    filename, size, mime = row
    size = int(size)
    if size <= 0:
        raise HTTPException(422, "Telegram media has no known size")

    raw_start = request.query_params.get("start")
    try:
        time_start = float(raw_start) if raw_start is not None else 0.0
    except (TypeError, ValueError):
        raise HTTPException(400, "Invalid start timestamp")
    if time_start < 0:
        raise HTTPException(400, "start must be zero or greater")

    if time_start > 0 and request.method != "HEAD":
        input_url = f"{BRIDGE_BASE_URL}/stream/{message_id}"
        cmd = [
            FFMPEG_BIN, "-hide_banner", "-loglevel", "error",
            "-ss", str(time_start), "-i", input_url,
            "-map", "0:v:0?", "-map", "0:a:0?", "-c", "copy",
            "-avoid_negative_ts", "make_zero",
            "-movflags", "frag_keyframe+empty_moov+default_base_moof",
            "-f", "mp4", "pipe:1",
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            raise HTTPException(503, "FFmpeg not found. Set FFMPEG_BIN in .env")

        async def ffmpeg_body():
            stderr_task = asyncio.create_task(proc.stderr.read())
            try:
                while True:
                    part = await proc.stdout.read(256 * 1024)
                    if not part:
                        break
                    yield part
                await proc.wait()
                err = await stderr_task
                if proc.returncode and err:
                    print(f"[wzgram FFmpeg seek {message_id}] {err.decode(errors='replace')}")
            finally:
                if proc.returncode is None:
                    proc.terminate()
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=3)
                    except asyncio.TimeoutError:
                        proc.kill()
                        await proc.wait()
                if not stderr_task.done():
                    stderr_task.cancel()

        return StreamingResponse(
            ffmpeg_body(),
            status_code=200,
            media_type="video/mp4",
            headers={
                "Cache-Control": "no-store",
                "Accept-Ranges": "none",
                "Content-Disposition": make_content_disposition(
                    f"{Path(filename).stem}_seek.mp4"
                ),
            },
        )

    start, end, status = parse_range(request.headers.get("range"), size)
    length = end - start + 1
    safe_name = Path(filename).name.replace('"', "")
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
        "Content-Disposition": make_content_disposition(safe_name),
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    }
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    if request.method == "HEAD":
        return Response(status_code=status, media_type=mime, headers=headers)

    message = await get_telegram_message(message_id)
    first_chunk = start // CHUNK_SIZE
    last_chunk = end // CHUNK_SIZE

    async def body():
        index = first_chunk

        while index <= last_chunk:
            key = (message_id, index)
            cached = await cache_get(key)

            if cached is None:
                async with telegram_stream_sem:
                    # Another request may have downloaded this chunk while we waited.
                    cached = await cache_get(key)

                    if cached is None:
                        retries = 0

                        while cached is None:
                            try:
                                received = bytearray()

                                async for part in client.stream_media(
                                    message,
                                    offset=index,
                                    limit=1,
                                ):
                                    received.extend(bytes(part))

                                if not received:
                                    raise RuntimeError(
                                        f"No data returned for chunk {index}"
                                    )

                                cached = bytes(received)
                                await cache_put(key, cached)

                            except asyncio.CancelledError:
                                raise
                            except Exception as exc:
                                retries += 1
                                print(
                                    f"[wzgram stream {message_id}] "
                                    f"chunk {index}, retry "
                                    f"{retries}/{MAX_RETRIES}: {exc}"
                                )
                                if retries >= MAX_RETRIES:
                                    raise
                                await asyncio.sleep(0.5 * retries)

            # Deliver data only AFTER releasing the Telegram semaphore.
            base = index * CHUNK_SIZE
            left = max(start - base, 0) if index == first_chunk else 0
            right = min(end - base + 1, len(cached))

            if right > left:
                yield cached[left:right]

            index += 1

    return StreamingResponse(
        body(),
        status_code=status,
        media_type=mime or mimetypes.guess_type(filename)[0] or "application/octet-stream",
        headers=headers,
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("bridge_wzgram_v2:app", host="127.0.0.1", port=8767, reload=False)