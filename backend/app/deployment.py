"""Loopback HTTPS deployment entry; migrations and account creation stay operator-only."""

import os
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.security.bootstrap import create_app as compose_app


def create_app():
    directory = Path(os.environ["PAPERPILOT_FRONTEND_DIR"]).resolve(strict=True)
    index = directory / "index.html"
    if not index.is_file() or not (directory / "assets").is_dir():
        raise RuntimeError("Build frontend assets before starting the local deployment")
    app = compose_app()
    app.mount("/assets", StaticFiles(directory=directory / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str):
        # Only published files and Vue routes; API misses must never become HTML successes.
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404)
        candidate = (directory / path).resolve()
        if candidate.is_relative_to(directory) and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index)

    return app
