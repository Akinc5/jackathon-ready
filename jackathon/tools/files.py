"""File tools for builder agents, locked to one folder.

A builder can only see and change files inside its own Workspace (e.g. the
generated project's `backend/`). Every path is resolved and checked, so
'../../secrets' or 'C:\\Windows' are refused before anything is touched.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from .base import Tool, ToolError

# Generated or installed folders: too big to read and never edited by hand.
SKIP_DIRS = {"node_modules", ".venv", "dist", "build", "__pycache__", ".pytest_cache", ".git"}
MAX_READ_CHARS = 40_000
MAX_WRITE_CHARS = 150_000


class Workspace:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def path(self, relative: str) -> Path:
        """Turn an agent-supplied path into a real path, refusing anything outside the workspace."""
        relative = relative.strip().replace("\\", "/")
        if not relative or relative.startswith("/") or ":" in relative:
            raise ToolError(f"Use a relative path inside your folder, like 'src/App.jsx' (got {relative!r}).")
        full = (self.root / relative).resolve()
        if not full.is_relative_to(self.root):
            raise ToolError(f"{relative!r} is outside your folder.")
        if SKIP_DIRS & set(full.relative_to(self.root).parts):
            raise ToolError(f"{relative!r} is inside a generated folder. Leave it alone.")
        return full

    def files(self) -> list[str]:
        """Every file in the workspace (relative paths), skipping generated folders."""
        out = []
        for p in sorted(self.root.rglob("*")):
            rel = p.relative_to(self.root)
            if p.is_file() and not SKIP_DIRS & set(rel.parts):
                out.append(rel.as_posix())
        return out


class NoArgs(BaseModel):
    pass


class PathArgs(BaseModel):
    path: str = Field(description="Relative path inside your folder, e.g. app/main.py")


class WriteArgs(BaseModel):
    path: str = Field(description="Relative path inside your folder, e.g. app/main.py")
    content: str = Field(description="The complete new file contents (this replaces the whole file).")


def file_tools(ws: Workspace) -> list[Tool]:
    """list/read/write/delete tools bound to one workspace."""

    async def list_files(_: NoArgs) -> str:
        files = ws.files()
        if not files:
            return "(empty folder)"
        lines = []
        for rel in files:
            try:
                n = len((ws.root / rel).read_text(encoding="utf-8").splitlines())
                lines.append(f"{rel}  ({n} lines)")
            except UnicodeDecodeError:
                lines.append(f"{rel}  (binary)")
        return "\n".join(lines)

    async def read_file(args: PathArgs) -> str:
        p = ws.path(args.path)
        if not p.is_file():
            raise ToolError(f"{args.path} doesn't exist. Use list_files to see what's there.")
        text = p.read_text(encoding="utf-8", errors="replace")
        if len(text) > MAX_READ_CHARS:
            text = text[:MAX_READ_CHARS] + "\n[...file truncated...]"
        return text

    async def write_file(args: WriteArgs) -> str:
        if len(args.content) > MAX_WRITE_CHARS:
            raise ToolError("File too large. Split it into smaller modules.")
        p = ws.path(args.path)
        p.parent.mkdir(parents=True, exist_ok=True)
        existed = p.exists()
        p.write_text(args.content, encoding="utf-8")
        return f"{'Updated' if existed else 'Created'} {args.path} ({len(args.content.splitlines())} lines)"

    async def delete_file(args: PathArgs) -> str:
        p = ws.path(args.path)
        if not p.is_file():
            raise ToolError(f"{args.path} doesn't exist.")
        p.unlink()
        return f"Deleted {args.path}"

    return [
        Tool("list_files", "List every file in your folder, with line counts.", NoArgs, list_files),
        Tool("read_file", "Read a file in your folder.", PathArgs, read_file),
        Tool("write_file", "Create or overwrite a file in your folder with complete contents.", WriteArgs, write_file),
        Tool("delete_file", "Delete a file in your folder.", PathArgs, delete_file),
    ]
