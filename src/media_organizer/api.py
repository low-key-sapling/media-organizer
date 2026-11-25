from __future__ import annotations

import os
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__
from .engine import Organizer
from .metadata import DEFAULT_MAPPING
from .paths import CATEGORIES, state_directory, web_root


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateJob(Model):
    source: str = Field(min_length=1)
    target: str = Field(min_length=1)
    verification: Literal["quick", "sampled", "full"] = "full"


class Execute(Model):
    verification: Literal["quick", "sampled", "full"] | None = None
    verify_only: bool = False


class Edit(Model):
    ids: list[int] = Field(min_length=1, max_length=1000)
    category: str | None = None
    month: str | None = None
    location: str | None = None


class OpenFolder(Model):
    kind: Literal["source", "target", "report"]


def create_app(directory: Path | None = None, organizer: Organizer | None = None) -> FastAPI:
    service = organizer or Organizer(directory or state_directory())

    @asynccontextmanager
    async def lifespan(app):
        yield
        service.close()

    app = FastAPI(title="Media Organizer", version=__version__, docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.organizer = service
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"])

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        origin = request.headers.get("origin")
        if request.method not in ("GET", "HEAD", "OPTIONS") and origin:
            parsed = urlparse(origin)
            if parsed.scheme != request.url.scheme or parsed.netloc != request.headers.get("host"):
                return JSONResponse({"detail": "仅允许本机界面的同源操作"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; connect-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'self'"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(ValueError)
    async def invalid(request, error):
        return JSONResponse({"detail": str(error)}, status_code=400)

    @app.exception_handler(OSError)
    async def filesystem_error(request, error):
        return JSONResponse({"detail": f"文件操作失败：{error}"}, status_code=400)

    @app.get("/api/meta")
    def meta():
        return {"version": __version__, "offline": True, "categories": list(CATEGORIES), "extension_mapping": DEFAULT_MAPPING, "metadata_available": service.reader.executable is not None, "places": service.places.info()}

    @app.get("/api/settings")
    def settings():
        return service.settings()

    @app.put("/api/settings")
    def update_settings(body: dict):
        return service.save_settings(body)

    @app.get("/api/directories")
    def directories(path: str = ""):
        if not path:
            roots = os.listdrives() if hasattr(os, "listdrives") else ["/", str(Path.home())]
            return {"path": "", "parent": "", "directories": [{"name": root, "path": root, "link": False} for root in roots]}
        directory = Path(path).expanduser().resolve()
        if not directory.is_dir():
            raise ValueError("目录不存在")
        children = []
        with os.scandir(directory) as entries:
            for entry in entries:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        child = Path(entry.path)
                        link = child.is_symlink() or (hasattr(child, "is_junction") and child.is_junction())
                        children.append({"name": entry.name, "path": str(child), "link": link})
                except OSError:
                    continue
        return {"path": str(directory), "parent": str(directory.parent) if directory.parent != directory else "", "directories": sorted(children, key=lambda item: item["name"].casefold())}

    @app.get("/api/jobs")
    def jobs():
        return service.store.jobs()

    @app.post("/api/jobs", status_code=202)
    def create(body: CreateJob):
        return service.create(body.source, body.target, body.verification)

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str):
        return {**service.store.job(job_id), "stats": service.store.stats(job_id)}

    @app.get("/api/jobs/{job_id}/files")
    def files(job_id: str, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200), search: str = "", category: str = "", status: str = ""):
        service.store.job(job_id)
        return service.store.entries(job_id, page, page_size, search, category, status)

    @app.patch("/api/jobs/{job_id}/files")
    def edit(job_id: str, body: Edit):
        changes = body.model_dump(exclude={"ids"}, exclude_none=True)
        return {"updated": service.edit(job_id, body.ids, changes)}

    @app.post("/api/jobs/{job_id}/execute", status_code=202)
    def execute(job_id: str, body: Execute):
        return service.execute(job_id, body.verification, verify_only=body.verify_only)

    @app.post("/api/jobs/{job_id}/pause")
    def pause(job_id: str):
        return service.stop(job_id)

    @app.get("/api/jobs/{job_id}/report.csv")
    def export_csv(job_id: str):
        job = service.store.job(job_id)
        if not job.get("report_path"):
            raise HTTPException(404, "任务尚未生成报告")
        path = Path(job["report_path"]) / "manifest.csv"
        if not path.is_file():
            raise HTTPException(404, "报告文件不存在")
        return FileResponse(path, media_type="text/csv; charset=utf-8", filename=f"media-organizer-{job_id}.csv")

    @app.post("/api/jobs/{job_id}/open")
    def open_folder(job_id: str, body: OpenFolder):
        job = service.store.job(job_id)
        path = Path(job["report_path"] if body.kind == "report" else job[body.kind])
        if not path.is_dir():
            raise ValueError("目录尚不存在")
        if os.name == "nt":
            os.startfile(path)
        else:
            subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"path": str(path)}

    root = web_root()
    if root.is_dir():
        app.mount("/", StaticFiles(directory=root, html=True), name="web")
    else:
        @app.get("/")
        def missing_frontend():
            return JSONResponse({"detail": "网页资源未构建，请在开发环境构建 frontend；正式发行包已包含网页资源"}, status_code=503)
    return app
