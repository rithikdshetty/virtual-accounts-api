from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db

router = APIRouter(tags=["Health"])


@router.get("/healthz", summary="Liveness probe")
def healthz(db: Session = Depends(get_db)) -> dict:
    """
    Returns 200 if the app is up AND the database is reachable.

    Note: technically a readiness check (touches the DB), not pure liveness.
    For Fly.io that distinction matters: liveness should NOT depend on
    downstream services. Will split into /healthz (pure) and /readyz (with
    DB) before deploy. Single endpoint is fine for local dev.
    """
    db.execute(text("SELECT 1"))
    return {"status": "ok", "version": settings.api_version}
