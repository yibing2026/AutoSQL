from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from app.api.routes import router as api_router
from app.core.config import get_settings
from app.core.database import initialize_database
from app.schemas.system import AppStatusResponse

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="A focused FastAPI service for automated clinical data imports.",
)


@app.on_event("startup")
def on_startup() -> None:
    initialize_database()


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")


@app.get("/health", tags=["system"])
def health() -> AppStatusResponse:
    return AppStatusResponse(
        status="ok",
        service=settings.app_name,
        database=settings.import_db_name,
        cached_summary_root=settings.import_cached_summary_root,
        downloaded_root=settings.import_downloaded_root,
    )


app.include_router(api_router)
