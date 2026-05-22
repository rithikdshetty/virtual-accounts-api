"""
Request-ID middleware. Generates a UUID per request, attaches it to
the response as `Request-Id` header, and makes it available to handlers
via request.state.request_id (used in error responses).
"""
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["Request-Id"] = request_id
        return response
