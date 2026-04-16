from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import get_settings
from app.db.session import engine, init_db
from app.services.recovery_service import recover_interrupted_analysis
from sqlmodel import Session

settings = get_settings()

app = FastAPI(title=settings.app_name)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router, prefix=settings.api_prefix)


@app.on_event("startup")
def on_startup() -> None:
    if settings.environment == "development":
        init_db()
    try:
        with Session(engine) as session:
            result = recover_interrupted_analysis(session)
            if result["interrupted_requeued"] or result["queued_dispatched"]:
                print(f"Analysis recovery: {result}")
    except Exception as exc:
        print(f"Analysis recovery skipped: {exc}")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
