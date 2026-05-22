from fastapi import FastAPI

from app.config import settings
from app.lib.request_id import RequestIdMiddleware
from app.routers import accounts, deposits, health

app = FastAPI(
    title="Virtual Accounts API",
    version=settings.api_version,
    description=(
        "REST API for virtual accounts on a master FBO with a double-entry "
        "ledger. See https://github.com/rithikdshetty/virtual-accounts-api "
        "for the full OpenAPI spec and design notes."
    ),
)

app.add_middleware(RequestIdMiddleware)

app.include_router(health.router)
app.include_router(accounts.router)
app.include_router(deposits.router)
