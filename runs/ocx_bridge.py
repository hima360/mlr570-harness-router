"""Lets harnesses inside Docker task containers reach opencodex on this Mac.

opencodex (127.0.0.1:10100) only serves requests whose Host is loopback, and a container reaches the Mac as
host.docker.internal, so Codex inside a task gets "403 cross-origin data-plane request blocked". This bridge listens on
127.0.0.1:10101 (Colima forwards host.docker.internal to the Mac's loopback; nothing on the LAN can reach it), checks a
local bearer token (OCX_BRIDGE_TOKEN in the git-ignored .env, so a web page cannot use it through DNS rebinding),
and forwards the request to opencodex with a loopback Host. Responses are streamed through unchanged.

Paths under /effort-medium/ (ZCode, whose headless mode always asks for "max") have the prefix dropped and any
reasoning_effort / reasoning.effort in the JSON body set to "medium", the effort every Phase 1 column runs at.

  set -a; . .env; set +a; nohup ../mlr570-harness-router/.venv/bin/python runs/ocx_bridge.py >> runs/ocx_bridge.log 2>&1 &
"""
import hmac
import json
import os

import aiohttp
from aiohttp import web

UPSTREAM = "http://127.0.0.1:10100"
TOKEN = os.environ["OCX_BRIDGE_TOKEN"]
DROP = {"host", "origin", "authorization", "content-length", "transfer-encoding", "connection"}


async def forward(request: web.Request) -> web.StreamResponse:
    got = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(got, TOKEN):
        return web.Response(status=401, text="bridge token required")
    if request.headers.get("Upgrade", "").lower() == "websocket":  # Codex then falls back to HTTPS streaming
        return web.Response(status=403, text="websocket not bridged")
    headers = {k: v for k, v in request.headers.items() if k.lower() not in DROP}
    headers["Host"] = "127.0.0.1:10100"
    body = await request.read()
    path = request.rel_url.raw_path_qs
    if path.startswith("/effort-medium/"):
        path = path[len("/effort-medium"):]
        try:
            doc = json.loads(body)
            if "reasoning_effort" in doc:
                doc["reasoning_effort"] = "medium"
            if isinstance(doc.get("reasoning"), dict) and "effort" in doc["reasoning"]:
                doc["reasoning"]["effort"] = "medium"
            body = json.dumps(doc).encode()
        except ValueError:
            print(f"effort-medium: body not JSON ({request.headers.get('Content-Encoding', 'plain')})", flush=True)
    async with request.app["session"].request(request.method, UPSTREAM + path,
                                              headers=headers, data=body or None) as up:
        resp = web.StreamResponse(status=up.status, headers={
            k: v for k, v in up.headers.items() if k.lower() not in DROP and k.lower() != "content-encoding"})
        await resp.prepare(request)
        async for chunk in up.content.iter_any():
            await resp.write(chunk)
        await resp.write_eof()
        return resp


async def session(app):
    app["session"] = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, sock_read=900),
                                           auto_decompress=False)
    yield
    await app["session"].close()


app = web.Application(client_max_size=64 * 1024 * 1024)
app.cleanup_ctx.append(session)
app.router.add_route("*", "/{tail:.*}", forward)

if __name__ == "__main__":
    web.run_app(app, host="127.0.0.1", port=10101, access_log=None)
