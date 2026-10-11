"""FastAPI application: API routers + the built frontend (frontend/dist) as a single-page app."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .api import books, courses, examples, keys, study
from .config import KEY_NAMES, PROJECT_ROOT, ROLES, settings
from .learning import grading
from .pipeline import jobs
from .schemas import Health

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)

FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    jobs.mark_interrupted_jobs()
    grading.warm_up()  # start the grading worker now so the first answer isn't slow  # builds of a previous process that never recorded their end
    try:
        yield
    finally:
        await jobs.shutdown()  # cancel this process's builds/top-ups and mark them interrupted (never left spinning)


app = FastAPI(title="Universal Academy", lifespan=lifespan)
app.include_router(books.router)
app.include_router(courses.router)
app.include_router(study.router)
app.include_router(keys.router)
app.include_router(examples.router)


@app.get("/api/health", response_model=Health)
def health() -> Health:
    configs = {role: settings.role_config(role) for role in ROLES}
    missing = [p for p in settings.providers_in_use if not settings.api_key(p)]
    return Health(
        openai=bool(settings.openai_api_key),
        anthropic=bool(settings.anthropic_api_key),
        provider=settings.llm_provider,
        llm_ready=settings.llm_ready,
        missing_keys=[] if settings.fake_llm or settings.agent_dir else [KEY_NAMES[p] for p in missing],
        nvidia=settings.has_nvidia,
        fake_llm=settings.fake_llm,
        parse_engine=settings.parse_engine,
        max_course_pages=settings.max_course_pages,
        models={role: f"{provider}:{model} ({effort})" for role, (provider, model, effort) in configs.items()},
        agent_route=bool(settings.agent_dir),
    )


@app.exception_handler(FileNotFoundError)
async def not_found(_, exc: FileNotFoundError):
    return JSONResponse(status_code=404, content={"detail": str(exc) or "Not found"})


if (FRONTEND_DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")


@app.get("/{full_path:path}", include_in_schema=False)
def spa(full_path: str):
    if full_path.startswith("api/"):
        return JSONResponse(status_code=404, content={"detail": "Not found"})
    dist = FRONTEND_DIST.resolve()
    if "\x00" in full_path:
        return JSONResponse(status_code=404, content={"detail": "Not found"})
    candidate = (dist / full_path).resolve()
    # Only ever serve files that live inside frontend/dist (blocks ../ and absolute-path tricks).
    if full_path and candidate.is_relative_to(dist) and candidate.is_file():
        return FileResponse(candidate)
    index = FRONTEND_DIST / "index.html"
    if index.exists():
        return FileResponse(index)
    return JSONResponse(
        status_code=200,
        content={"detail": "Frontend not built yet. Run `npm run build` in frontend/ or use the Vite dev server."},
    )
