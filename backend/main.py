import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.database import init_db
from backend.config import init_settings

logger = logging.getLogger(__name__)


def create_app(data_dir: str | None = None, run_background: bool = True) -> FastAPI:
    """Create and configure the FastAPI application."""
    if data_dir is None:
        data_dir = os.environ.get("RECEIPTORY_DATA_DIR", os.path.join(os.getcwd(), "data"))

    background_tasks: list[asyncio.Task] = []

    # Initialize DB and settings eagerly (not in lifespan)
    # so it's done before any request, including in tests
    from backend.database import get_db_path
    if get_db_path() is None:
        db_path = os.path.join(data_dir, "receiptory.db")
        init_db(db_path)
        init_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Configure logging
        from backend.config import get_setting
        log_level = get_setting("log_level")
        os.makedirs(os.path.join(data_dir, "logs"), exist_ok=True)
        logging.basicConfig(
            level=getattr(logging, log_level, logging.INFO),
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            handlers=[
                logging.StreamHandler(),
                logging.FileHandler(os.path.join(data_dir, "logs", "receiptory.log")),
            ],
        )

        if run_background:
            from backend.processing.queue import run_queue_loop
            from backend.backup.scheduler import run_backup_scheduler
            queue_task = asyncio.create_task(run_queue_loop(data_dir))
            backup_task = asyncio.create_task(run_backup_scheduler(data_dir))
            background_tasks.extend([queue_task, backup_task])

        app.state.data_dir = data_dir
        yield

        for task in background_tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    app = FastAPI(title="Receiptory", version="0.1.0", lifespan=lifespan)

    # Set data_dir immediately so it's available even without lifespan
    app.state.data_dir = data_dir

    # CORS for dev mode
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register API routes
    from backend.api.auth import router as auth_router
    app.include_router(auth_router, prefix="/api/auth", tags=["auth"])

    from backend.api.upload import router as upload_router
    app.include_router(upload_router, prefix="/api", tags=["upload"])

    from backend.api.documents import router as documents_router
    app.include_router(documents_router, prefix="/api", tags=["documents"])

    from backend.api.categories import router as categories_router
    app.include_router(categories_router, prefix="/api", tags=["categories"])

    from backend.api.settings import router as settings_router
    app.include_router(settings_router, prefix="/api", tags=["settings"])

    from backend.api.queue import router as queue_router
    app.include_router(queue_router, prefix="/api", tags=["queue"])

    from backend.api.stats import router as stats_router
    app.include_router(stats_router, prefix="/api", tags=["stats"])

    from backend.api.logs import router as logs_router
    app.include_router(logs_router, prefix="/api", tags=["logs"])

    from backend.api.export import router as export_router
    app.include_router(export_router, prefix="/api", tags=["export"])

    from backend.api.backup import router as backup_router
    app.include_router(backup_router, prefix="/api", tags=["backup"])

    # Serve frontend static files in production (skip in dev when Vite handles frontend)
    if not os.environ.get("RECEIPTORY_DEV"):
        frontend_dir = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
        if os.path.exists(frontend_dir):
            app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")

    return app
