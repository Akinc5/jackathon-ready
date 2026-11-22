"""Serves the API and the built frontend from one process.

Used by the deployed container and by Jackathon's browser smoke test.
Don't put routes here: the app's API lives in app/main.py.
"""

import os
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.main import app

STATIC = Path(os.environ.get("STATIC_DIR", Path(__file__).parent / "static")).resolve()

if (STATIC / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def frontend(path: str):
    """Any non-API path returns the React app, which then shows the right screen."""
    if path.startswith("api/"):
        raise HTTPException(status_code=404)
    file = (STATIC / path).resolve()
    if path and file.is_file() and file.is_relative_to(STATIC):
        return FileResponse(file)
    return FileResponse(STATIC / "index.html")
