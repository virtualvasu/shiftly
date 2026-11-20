"""Every failure reaches the client as plain JSON. Traces stay in the server log."""

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("app")

SERVER_ERROR = (
    "Something went wrong on our side. Nothing was changed. "
    "Please try again, or call the transport desk if it's urgent."
)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException):
        detail = exc.detail if isinstance(exc.detail, str) else "Request failed."
        if exc.status_code == 404 and detail == "Not Found":
            detail = "Not found."
        return JSONResponse({"error": detail}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        details = [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]
        return JSONResponse(
            {"error": "That request wasn't valid.", "details": details}, status_code=422
        )

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        ref = uuid.uuid4().hex[:8]
        log.exception("unhandled error ref=%s path=%s", ref, request.url.path)
        return JSONResponse({"error": SERVER_ERROR, "ref": ref}, status_code=500)
