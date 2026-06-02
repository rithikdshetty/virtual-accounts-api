import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.lib.request_id import RequestIdMiddleware
from app.lib.webhook_worker import start_worker, stop_worker
from app.routers import (
    accounts,
    deposits,
    events,
    health,
    transfers,
    webhook_endpoints,
    withdrawals,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_worker()
    yield
    stop_worker()


app = FastAPI(
    title="Virtual Accounts API",
    version=settings.api_version,
    description=(
        "REST API for virtual accounts on a master FBO with a double-entry "
        "ledger. See https://github.com/rithikdshetty/virtual-accounts-api "
        "for the full OpenAPI spec and design notes."
    ),
    lifespan=lifespan,
)

app.add_middleware(RequestIdMiddleware)

app.include_router(health.router)
app.include_router(accounts.router)
app.include_router(deposits.router)
app.include_router(transfers.router)
app.include_router(withdrawals.router)
app.include_router(events.router)
app.include_router(webhook_endpoints.router)

# Serve the static marketing site + interactive console from the same origin
# as the API. Mounted LAST so all API routers above take precedence; only
# unmatched paths fall through to the static files. Same-origin means the
# browser console can call the API with no CORS configuration at all.
# `html=True` serves index.html at "/" and resolves clean paths like /console.
_WEB_DIR = Path(__file__).resolve().parent.parent / "web"
if _WEB_DIR.is_dir():
    # Clean extensionless URL for the console (StaticFiles won't add ".html").
    @app.get("/console", include_in_schema=False)
    def _console() -> FileResponse:
        return FileResponse(_WEB_DIR / "console.html")

    app.mount("/", StaticFiles(directory=str(_WEB_DIR), html=True), name="site")
