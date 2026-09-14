import asyncio
import contextlib
import hmac
import json
import logging
import math
import os
import re
import secrets
import shutil
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import unquote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from . import media

ROOT = Path(__file__).resolve().parent
DATA = Path(os.getenv("DATA_DIR", "/tmp/hn-voice-studio")).resolve()
MAX_UPLOAD = int(os.getenv("MAX_UPLOAD_MB", "250")) * 1024 * 1024
TTL = int(os.getenv("RETENTION_SECONDS", "7200"))
ACCESS_KEY = os.getenv("APP_ACCESS_KEY", "")
SIGNER = URLSafeTimedSerializer(os.getenv("SESSION_SECRET") or secrets.token_urlsafe(48), salt="hn-voice-v1")
PROJECTS: dict[str, dict] = {}
JOBS: dict[str, dict] = {}
QUEUE: asyncio.Queue = asyncio.Queue(maxsize=8)
LOGIN_ATTEMPTS: dict[str, list[float]] = {}
COOKIE = "hn_voice_session"
ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
ACTIVE = {"uploading", "queued", "running"}
DEFAULTS = {"pitch": 0.0, "noise": 8.0, "bass": 0.0, "mid": 0.0, "treble": 0.0,
            "gain": 0.0, "fade": 0.0, "highpass": True, "compress": True, "normalize": True}


def remove_project(project):
    PROJECTS.pop(project["id"], None)
    for key in [key for key, job in JOBS.items() if job["project_id"] == project["id"]]:
        JOBS.pop(key, None)
    shutil.rmtree(project["folder"], ignore_errors=True)


async def worker():
    while True:
        job, settings = await QUEUE.get()
        project = PROJECTS.get(job["project_id"])
        try:
            if not project or job.get("cancel"):
                raise media.Cancelled()
            job.update(state="running", progress=0)
            if job["kind"] == "prepare":
                await media.prepare(project, job)
            else:
                await media.render(project, job, settings)
            job.update(state="done", progress=100, stage="Hoàn tất")
        except media.Cancelled:
            job.update(state="cancelled", stage="Đã hủy")
            if project:
                project["state"] = "done" if project.get("output") else ("ready" if project.get("meta") and (Path(project["folder"]) / "preview.mp4").exists() else "error")
        except Exception as exc:
            logging.exception("Media job failed: %s", job["id"])
            job.update(state="error", stage="Xử lý chưa thành công",
                       error=str(exc) if isinstance(exc, media.MediaError) else "Máy chủ gặp lỗi xử lý. Bạn có thể thử lại.")
            if project:
                project["state"] = "error" if job["kind"] == "prepare" else ("done" if project.get("output") else "ready")
        finally:
            if project and project.get("delete"):
                remove_project(project)
            QUEUE.task_done()


async def janitor():
    while True:
        await asyncio.sleep(30)
        now = time.time()
        for project in list(PROJECTS.values()):
            if project["expires_at"] < now and project["state"] not in ACTIVE:
                remove_project(project)
        for ip in list(LOGIN_ATTEMPTS):
            LOGIN_ATTEMPTS[ip] = [ts for ts in LOGIN_ATTEMPTS[ip] if now - ts < 600]
            if not LOGIN_ATTEMPTS[ip]:
                LOGIN_ATTEMPTS.pop(ip, None)


@asynccontextmanager
async def lifespan(app):
    if os.getenv("REQUIRE_ACCESS_KEY", "").lower() == "true" and len(ACCESS_KEY) < 12:
        raise RuntimeError("APP_ACCESS_KEY must contain at least 12 characters.")
    DATA.mkdir(parents=True, exist_ok=True)
    # This edition explicitly provides temporary storage. Clean abandoned task directories.
    for folder in DATA.iterdir():
        if folder.is_dir() and ID_PATTERN.fullmatch(folder.name):
            shutil.rmtree(folder, ignore_errors=True)
    media_version = await asyncio.create_subprocess_exec(media.FFMPEG, "-version", stdout=asyncio.subprocess.DEVNULL)
    if await media_version.wait() != 0:
        raise RuntimeError("FFmpeg unavailable")
    tasks = [asyncio.create_task(worker()), asyncio.create_task(janitor())]
    yield
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


app = FastAPI(title="HN AI Voice Studio Pro", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


def owner(request: Request) -> str:
    try:
        token = SIGNER.loads(request.cookies.get(COOKIE, ""), max_age=14 * 86400)
        if isinstance(token, str) and ID_PATTERN.fullmatch(token):
            return token
    except (BadSignature, SignatureExpired):
        pass
    raise HTTPException(401, "Phiên làm việc đã hết hạn. Hãy đăng nhập lại.")


@app.middleware("http")
async def safety_headers(request, call_next):
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        origin = request.headers.get("origin")
        # Same-origin requests only. Explicit origin supports reverse proxy deployments.
        allowed = os.getenv("PUBLIC_ORIGIN") or str(request.base_url).rstrip("/")
        if origin and origin.rstrip("/") != allowed.rstrip("/"):
            return JSONResponse({"detail": "Yêu cầu khác nguồn bị từ chối."}, status_code=403)
        if request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "Yêu cầu khác nguồn bị từ chối."}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(HTTPException)
async def http_error(request, exc):
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


@app.get("/")
async def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/healthz")
async def health():
    return {"status": "ok", "engine": "ffmpeg-dsp", "ai_voice_conversion": False}


@app.get("/api/config")
async def config():
    return {"name": "HN AI VOICE STUDIO PRO", "version": "0.1.0",
            "max_upload_mb": MAX_UPLOAD // 1024 // 1024, "max_minutes": media.MAX_SECONDS // 60,
            "retention_seconds": TTL, "password_required": bool(ACCESS_KEY),
            "defaults": DEFAULTS, "storage": "temporary-server",
            "ai_voice_conversion": False}


async def small_json(request: Request):
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 8192:
            raise HTTPException(413, "Yêu cầu quá lớn.")
    try:
        data = json.loads(raw or b"{}")
        if not isinstance(data, dict):
            raise ValueError()
        return data
    except (ValueError, TypeError):
        raise HTTPException(400, "Dữ liệu gửi lên không hợp lệ.")


@app.post("/api/session")
async def session(request: Request):
    body = await small_json(request)
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    attempts = LOGIN_ATTEMPTS.setdefault(ip, [])
    attempts[:] = [ts for ts in attempts if now - ts < 600]
    if len(attempts) >= 12:
        raise HTTPException(429, "Hãy đợi vài phút trước khi đăng nhập lại.")
    supplied = body.get("password", "")
    if not isinstance(supplied, str) or (ACCESS_KEY and not hmac.compare_digest(supplied.encode(), ACCESS_KEY.encode())):
        attempts.append(now)
        raise HTTPException(401, "Mật khẩu chưa đúng.")
    try:
        uid = owner(request)
    except HTTPException:
        uid = uuid.uuid4().hex
    response = JSONResponse({"ok": True})
    response.set_cookie(COOKIE, SIGNER.dumps(uid), httponly=True, secure=request.url.scheme == "https",
                        samesite="strict", max_age=14 * 86400, path="/")
    return response


def find_project(project_id, request):
    uid = owner(request)
    project = PROJECTS.get(project_id)
    if not project or project["owner"] != uid or project.get("delete"):
        raise HTTPException(404, "Video không tồn tại hoặc đã hết thời gian lưu.")
    return project


def view_project(project):
    return {key: project.get(key) for key in ("id", "name", "size", "created_at", "expires_at",
                                             "state", "meta", "job_id", "output")}


def new_job(project, kind):
    job = {"id": uuid.uuid4().hex, "project_id": project["id"], "kind": kind, "state": "queued",
           "progress": 0, "stage": "Đang chờ lượt xử lý", "error": None, "cancel": False}
    JOBS[job["id"]] = job
    project["job_id"] = job["id"]
    project["state"] = "queued"
    return job


@app.get("/api/projects")
async def projects(request: Request):
    uid = owner(request)
    return [view_project(p) for p in sorted(PROJECTS.values(), key=lambda p: p["created_at"], reverse=True)
            if p["owner"] == uid and not p.get("delete")]


@app.get("/api/projects/{project_id}")
async def project_detail(project_id: str, request: Request):
    return view_project(find_project(project_id, request))


@app.post("/api/projects", status_code=202)
async def upload(request: Request):
    uid = owner(request)
    if QUEUE.full() or sum(p["state"] in ACTIVE for p in PROJECTS.values()) >= 8:
        raise HTTPException(429, "Máy chủ đang đủ lượt xử lý. Vui lòng thử lại sau.")
    if sum(p["owner"] == uid for p in PROJECTS.values()) >= 4:
        raise HTTPException(429, "Mỗi phiên giữ tối đa 4 video. Hãy xóa một video trước.")
    if len(PROJECTS) >= 24 or shutil.disk_usage(DATA).free < MAX_UPLOAD + 1024**3:
        raise HTTPException(507, "Máy chủ chưa đủ dung lượng. Hãy thử lại sau.")
    try:
        announced_size = int(request.headers.get("content-length", "0"))
    except ValueError:
        raise HTTPException(400, "Dung lượng file không hợp lệ.")
    if announced_size > MAX_UPLOAD:
        raise HTTPException(413, f"Video vượt giới hạn {MAX_UPLOAD // 1024 // 1024} MB.")
    name = unquote(request.headers.get("x-file-name", "video.mp4"))
    name = re.sub(r"[\x00-\x1f\x7f/\\]", "_", name)[:160] or "video.mp4"
    if Path(name).suffix.lower() not in {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}:
        raise HTTPException(415, "Hãy chọn MP4, MOV, MKV, WEBM, M4V hoặc AVI.")
    pid = uuid.uuid4().hex
    folder = DATA / pid
    folder.mkdir()
    project = {"id": pid, "owner": uid, "name": name, "folder": str(folder), "size": 0,
               "created_at": time.time(), "expires_at": time.time() + TTL, "state": "uploading"}
    PROJECTS[pid] = project
    size = 0
    try:
        async with asyncio.timeout(600):
            with (folder / "source").open("wb") as output:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_UPLOAD:
                        raise HTTPException(413, f"Video vượt giới hạn {MAX_UPLOAD // 1024 // 1024} MB.")
                    output.write(chunk)
        if size < 32:
            raise HTTPException(400, "File video rỗng hoặc chưa tải đầy đủ.")
        project["size"] = size
        job = new_job(project, "prepare")
        try:
            QUEUE.put_nowait((job, None))
        except asyncio.QueueFull:
            raise HTTPException(429, "Hàng đợi đã đầy. Hãy thử lại sau.")
        return {"project": view_project(project), "job": job}
    except BaseException:
        remove_project(project)
        raise


def validate_settings(data):
    limits = {"pitch": (-12, 12), "noise": (0, 30), "bass": (-12, 12),
              "mid": (-12, 12), "treble": (-12, 12), "gain": (-12, 12), "fade": (0, 3)}
    settings = dict(DEFAULTS)
    if set(data) - set(DEFAULTS):
        raise HTTPException(422, "Có thông số chưa được hỗ trợ.")
    for key, value in data.items():
        if key in limits:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise HTTPException(422, f"Thông số {key} phải là số hợp lệ.")
            if not limits[key][0] <= value <= limits[key][1]:
                raise HTTPException(422, f"Thông số {key} ngoài giới hạn.")
            settings[key] = float(value)
        elif not isinstance(value, bool):
            raise HTTPException(422, f"Thông số {key} phải là bật hoặc tắt.")
        else:
            settings[key] = value
    return settings


@app.post("/api/projects/{project_id}/render", status_code=202)
async def render_video(project_id: str, request: Request):
    project = find_project(project_id, request)
    settings = validate_settings(await small_json(request))
    if project["state"] not in {"ready", "done"}:
        raise HTTPException(409, "Video cần chuẩn bị xong trước khi xử lý.")
    if not project["meta"]["has_audio"]:
        raise HTTPException(422, "Video không có âm thanh để đổi giọng.")
    if QUEUE.full():
        raise HTTPException(429, "Máy chủ đang bận. Hãy thử lại sau.")
    # There are no await points between the state check and queue insertion.
    job = new_job(project, "render")
    QUEUE.put_nowait((job, settings))
    return {"job": job}


@app.get("/api/jobs/{job_id}")
async def job_status(job_id: str, request: Request):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Không tìm thấy lượt xử lý.")
    project = find_project(job["project_id"], request)
    return {"job": {k: v for k, v in job.items() if k != "cancel"}, "project": view_project(project)}


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, request: Request):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Không tìm thấy lượt xử lý.")
    find_project(job["project_id"], request)
    job["cancel"] = True
    return {"ok": True}


@app.delete("/api/projects/{project_id}")
async def delete_video(project_id: str, request: Request):
    project = find_project(project_id, request)
    job = JOBS.get(project.get("job_id"))
    if job and job["state"] in {"queued", "running"}:
        project["delete"] = True
        job["cancel"] = True
    else:
        remove_project(project)
    return {"ok": True}


@app.get("/api/projects/{project_id}/media/{kind}")
async def serve_media(project_id: str, kind: str, request: Request, download: bool = False):
    project = find_project(project_id, request)
    if kind not in {"preview", "output"}:
        raise HTTPException(404, "Không tìm thấy video.")
    path = Path(project["folder"]) / f"{kind}.mp4"
    if not path.exists():
        raise HTTPException(404, "Video chưa sẵn sàng.")
    filename = f"{Path(project['name']).stem}-HN-VOICE.mp4" if download else None
    return FileResponse(path, media_type="video/mp4", filename=filename,
                        content_disposition_type="attachment" if download else "inline")


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
