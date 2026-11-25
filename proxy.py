
import asyncio
import gzip
import sqlite3
from pathlib import Path
from aiohttp import web, ClientSession, WSMsgType

STASH = "http://127.0.0.1:9999"
BRIDGE = "http://127.0.0.1:8767"  # wzgram Bridge v2
BROWSER_ORIGIN = "http://127.0.0.1:9998"

session = None

HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate",
    "proxy-authorization", "te", "trailers",
    "transfer-encoding", "upgrade"
}


async def websocket_proxy(request):
    upstream_url = "ws://127.0.0.1:9999" + str(request.rel_url)

    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in HOP_HEADERS
        and k.lower() not in {"host", "origin"}
    }
    headers["Origin"] = STASH

    client_ws = web.WebSocketResponse(
        protocols=("graphql-transport-ws",)
    )
    await client_ws.prepare(request)
    
    try:
        async with session.ws_connect(
            upstream_url,
            headers=headers,
            protocols=("graphql-transport-ws",)
        ) as upstream_ws:

            async def client_to_upstream():
                async for msg in client_ws:
                    if msg.type == WSMsgType.TEXT:
                        await upstream_ws.send_str(msg.data)
                    elif msg.type == WSMsgType.BINARY:
                        await upstream_ws.send_bytes(msg.data)
                    elif msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                        await upstream_ws.close()
                        break

            async def upstream_to_client():
                async for msg in upstream_ws:
                    if msg.type == WSMsgType.TEXT:
                        await client_ws.send_str(msg.data)
                    elif msg.type == WSMsgType.BINARY:
                        await client_ws.send_bytes(msg.data)
                    elif msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                        await client_ws.close()
                        break

            await asyncio.gather(
                client_to_upstream(),
                upstream_to_client()
            )

    except Exception as exc:
        print("WebSocket proxy error:", repr(exc))
        await client_ws.close()

    return client_ws


async def proxy_request(request, url, fallback_url=None):
    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in HOP_HEADERS
        and k.lower() not in {"host", "origin", "content-length"}
    }

    headers["Host"] = "127.0.0.1:9999"

    if "Origin" in request.headers:
        headers["Origin"] = STASH

    if "Referer" in request.headers:
        headers["Referer"] = STASH + "/"

    async with session.request(
        request.method,
        url,
        headers=headers,
        data=request.content if request.can_read_body else None,
        allow_redirects=False
    ) as upstream:

        # If the local Stash stream is unavailable, retry using the Telegram bridge.
        if fallback_url and upstream.status in (404, 410, 500, 502):
            upstream.release()
            return await proxy_request(request, fallback_url)

        content_type = upstream.headers.get("Content-Type", "").lower()

        response_headers = {
            k: v for k, v in upstream.headers.items()
            if k.lower() not in HOP_HEADERS
        }

        # Video responses must not be cached: Stash files may be replaced
        # while keeping the same scene URL, and stale byte ranges can break
        # browser media demuxing. Apply this to local and Telegram streams.
        if content_type.startswith("video/") or request.path.startswith(
            ("/smart-stream/", "/telegram-stream/")
        ):
            response_headers["Cache-Control"] = (
                "no-store, no-cache, must-revalidate, max-age=0"
            )
            response_headers["Pragma"] = "no-cache"
            response_headers["Expires"] = "0"

        csp = response_headers.get("Content-Security-Policy")

        if csp:
            csp = csp.replace(
                "connect-src data: 'self' ws: wss:",
                "connect-src data: 'self' http://127.0.0.1:9999 ws: wss: ws://127.0.0.1:9999"
            )
            csp = csp.replace(
                "script-src 'self'",
                "script-src 'self' http://127.0.0.1:9999"
            )
            csp = csp.replace(
                "media-src blob: 'self'",
                "media-src blob: 'self' http://127.0.0.1:9999"
            )
            response_headers["Content-Security-Policy"] = csp

        location = response_headers.get("Location")
        if location:
            response_headers["Location"] = (
                location.replace("http://localhost:9999", BROWSER_ORIGIN)
                .replace("http://127.0.0.1:9999", BROWSER_ORIGIN)
            )

        # Rewrite absolute Stash URLs in text/API responses.
        if any(t in content_type for t in (
            "text/", "application/json",
            "javascript", "application/xml"
        )):
            body = await upstream.read()

            encoding = upstream.headers.get("Content-Encoding", "").lower()
            if encoding == "gzip":
                body = gzip.decompress(body)

            body = body.replace(
                b"http://localhost:9999",
                BROWSER_ORIGIN.encode()
            ).replace(
                b"http://127.0.0.1:9999",
                BROWSER_ORIGIN.encode()
            ).replace(
                b"ws://localhost:9999",
                b"ws://127.0.0.1:9998"
            ).replace(
                b"ws://127.0.0.1:9999",
                b"ws://127.0.0.1:9998"
            )

            response_headers = {
                k: v for k, v in response_headers.items()
                if k.lower() not in {"content-encoding", "content-length"}
            }
            response_headers["Content-Length"] = str(len(body))

            return web.Response(
                body=body,
                status=upstream.status,
                headers=response_headers
            )

        response = web.StreamResponse(
            status=upstream.status,
            headers=response_headers
        )
        await response.prepare(request)

        if request.method != "HEAD":
            async for chunk in upstream.content.iter_chunked(256 * 1024):
                await response.write(chunk)

        await response.write_eof()
        return response


async def telegram_stream(request):
    # Preserve Stash query parameters such as ?start=<seconds> when
    # forwarding to the Telegram bridge.
    url = f"{BRIDGE}/stream/{request.match_info['message_id']}"
    if request.rel_url.query_string:
        url += f"?{request.rel_url.query_string}"
    return await proxy_request(request, url)


async def smart_stream(request):
    scene_id = request.match_info["scene_id"]

    query = f"?{request.rel_url.query_string}" if request.rel_url.query_string else ""
    local_url = f"{STASH}/scene/{scene_id}/stream{query}"

    # Look up Telegram only as a fallback.
    db_path = Path(__file__).resolve().parent / "bridge.sqlite3"

    with sqlite3.connect(db_path) as db:
        result = db.execute(
            "SELECT message_id FROM scene_telegram_map WHERE scene_id = ?",
            (scene_id,)
        ).fetchone()

    telegram_url = (
        f"{BRIDGE}/stream/{result[0]}{query}"
        if result is not None else None
    )

    # Always try the local Stash stream first.
    return await proxy_request(
        request,
        local_url,
        fallback_url=telegram_url
    )


async def stash_request(request):
    if request.headers.get("Upgrade", "").lower() == "websocket":
        return await websocket_proxy(request)

    url = f"{STASH}{request.rel_url}"
    return await proxy_request(request, url)


async def startup(app):
    global session
    session = ClientSession(auto_decompress=False)


async def cleanup(app):
    await session.close()


app = web.Application()
app.router.add_route(
    "*", "/telegram-stream/{message_id:\\d+}", telegram_stream
)
app.router.add_route("*", "/smart-stream/{scene_id:\\d+}", smart_stream)
app.router.add_route("*", "/{tail:.*}", stash_request)

app.on_startup.append(startup)
app.on_cleanup.append(cleanup)

if __name__ == "__main__":
    web.run_app(app, host="127.0.0.1", port=9998)
