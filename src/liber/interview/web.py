"""The local interview page and its API (127.0.0.1 only; every /api/* call needs the session token)."""

import hmac
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

from liber.errors import LiberError
from liber.interview.live import LiveError
from liber.interview.session import InterviewSession

STATIC_DIR = Path(__file__).parent / "static"


def create_app(session: InterviewSession, token: str, *, static_dir: Path = STATIC_DIR) -> Starlette:
    def authorized(request: Request) -> bool:
        return hmac.compare_digest(request.headers.get("X-Liber-Token", ""), token)

    def forbidden() -> JSONResponse:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    def failure(exc: LiberError) -> JSONResponse:
        return JSONResponse({"error": str(exc)}, status_code=502 if isinstance(exc, LiveError) else 409)

    async def body_text(request: Request, field: str) -> str:
        try:
            data = await request.json()
        except ValueError:
            return ""
        value = data.get(field) if isinstance(data, dict) else None
        return value if isinstance(value, str) else ""

    async def index(request: Request):
        return FileResponse(static_dir / "index.html", media_type="text/html")

    async def app_js(request: Request):
        return FileResponse(static_dir / "app.js", media_type="text/javascript")

    async def style_css(request: Request):
        return FileResponse(static_dir / "style.css", media_type="text/css")

    async def connect(request: Request, resume: bool):
        if not authorized(request):
            return forbidden()
        sdp = await body_text(request, "sdp")
        if not sdp.strip():
            return JSONResponse({"error": "an SDP offer is required"}, status_code=400)
        try:
            answer = await (session.resume(sdp) if resume else session.start(sdp))
        except LiberError as exc:
            return failure(exc)
        return JSONResponse({"sdp": answer}, status_code=201)

    async def api_session(request: Request):
        return await connect(request, resume=False)

    async def api_resume(request: Request):
        return await connect(request, resume=True)

    async def api_note(request: Request):
        if not authorized(request):
            return forbidden()
        try:
            await session.add_note(await body_text(request, "text"))
        except LiberError as exc:
            return failure(exc)
        return JSONResponse({"ok": True})

    async def api_hold(request: Request):
        if not authorized(request):
            return forbidden()
        try:
            return JSONResponse({"held": await session.toggle_hold()})
        except LiberError as exc:
            return failure(exc)

    async def api_status(request: Request):
        if not authorized(request):
            return forbidden()
        return JSONResponse(session.heartbeat())

    async def api_end(request: Request):
        if not authorized(request):
            return forbidden()
        result = await session.end("ended by user")
        return JSONResponse({
            "transcript": str(result.transcript) if result.transcript else None,
            "notes": str(result.notes) if result.notes else None,
        })

    return Starlette(routes=[
        Route("/", index),
        Route("/app.js", app_js),
        Route("/style.css", style_css),
        Route("/api/session", api_session, methods=["POST"]),
        Route("/api/resume", api_resume, methods=["POST"]),
        Route("/api/note", api_note, methods=["POST"]),
        Route("/api/hold", api_hold, methods=["POST"]),
        Route("/api/status", api_status, methods=["GET"]),
        Route("/api/end", api_end, methods=["POST"]),
    ])
