import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

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
