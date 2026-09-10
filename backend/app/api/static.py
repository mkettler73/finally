"""Serving the built Next.js export (API_CONTRACT §8).

The frontend is a static export copied to ``backend/static`` by the Dockerfile
(overridable with ``FINALLY_STATIC_DIR``). ``/api/*`` always wins because the
API routers are registered before this mount, and an unmatched path under
``/api/`` is answered with the JSON error envelope rather than the HTML shell.
Any other unmatched path gets Next's own ``404.html``; the export has no
client-side routes, so there is no deep link an ``index.html`` fallback would
rescue. When the directory is absent — local backend-only development — the app
still starts and logs one warning.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

STATIC_DIR_ENV = "FINALLY_STATIC_DIR"

# backend/static — sibling of the `app` package, which is where the Docker
# build stages the Next.js `out/` directory.
DEFAULT_STATIC_DIR = Path(__file__).resolve().parents[2] / "static"


def resolve_static_dir() -> Path:
    override = os.environ.get(STATIC_DIR_ENV, "").strip()
    return Path(override) if override else DEFAULT_STATIC_DIR


class SpaStaticFiles(StaticFiles):
    """StaticFiles that answers unknown /api/ paths with JSON, not HTML."""

    async def get_response(self, path: str, scope: Any) -> Response:
        # This check MUST come before delegating to StaticFiles, not in an
        # exception handler after it. With html=True, StaticFiles looks for
        # "404.html" and *returns* it rather than raising HTTPException -- and
        # Next.js emits a 404.html -- so an `except StarletteHTTPException`
        # block never runs. A previous version of this class did exactly that
        # and was entirely dead code: every unmatched /api/ path was answered
        # with the HTML error page, which is the one thing it existed to stop.
        #
        # StaticFiles hands us an OS-native path: on Windows that is
        # "api\thing", so comparing against "api/" alone silently fails.
        url_path = path.replace("\\", "/")
        if url_path == "api" or url_path.startswith("api/"):
            # Never answer an unknown API path with the HTML shell: the caller
            # is expecting JSON and would try to parse a document.
            return JSONResponse(
                status_code=404,
                content={
                    "detail": {
                        "code": "NOT_FOUND",
                        "message": f"No such endpoint: /{url_path}",
                    }
                },
            )

        # Anything else falls through to StaticFiles, which serves the file if
        # it exists and Next's 404.html otherwise. Per API_CONTRACT §8 that 404
        # is deliberate: this export has no client-side routes, so there is no
        # deep link to rescue with an index.html fallback.
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code != 404:
                raise
            # Reached only when the export has no 404.html of its own.
            return await super().get_response("index.html", scope)


def mount_static(app: FastAPI) -> bool:
    """Mount the static export at ``/`` if it exists. Returns whether it did."""
    static_dir = resolve_static_dir()
    if not static_dir.is_dir():
        logger.warning(
            "No static frontend at %s - serving the API only. Build the frontend or set %s.",
            static_dir,
            STATIC_DIR_ENV,
        )
        return False

    app.mount("/", SpaStaticFiles(directory=static_dir, html=True), name="static")
    logger.info("Serving static frontend from %s", static_dir)
    return True
