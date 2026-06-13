"""file system mcp tool server - sandboxed to session directories with resource limits."""

from __future__ import annotations

from pathlib import Path

from voicr.config import (
    SESSIONS_DIR,
    MAX_FILE_SIZE_BYTES,
    MAX_FILES_PER_SESSION,
    MAX_TOTAL_BYTES_PER_SESSION,
    MAX_FILE_PATH_DEPTH,
)
from voicr.sanitizer import InputSanitizer


class FileSystemTool:
    """mcp-compliant file system tool with path traversal and resource limits."""

    def __init__(self) -> None:
        self.base_dir = SESSIONS_DIR
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _resolve(self, session_id: str, filepath: str) -> Path:
        session_dir = self.base_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        filepath = InputSanitizer.sanitize_filepath(filepath)
        resolved = (session_dir / filepath).resolve()

        # use Path.is_relative_to for secure containment check
        try:
            resolved.relative_to(session_dir.resolve())
        except ValueError:
            raise PermissionError(f"Path traversal blocked: {filepath}")

        # reject symlinks
        if resolved.exists() and resolved.is_symlink():
            raise PermissionError("Symlinks are not allowed")

        return resolved

    def _check_session_limits(self, session_id: str) -> None:
        """enforce per-session file count and total byte limits."""
        session_dir = self.base_dir / session_id
        if not session_dir.exists():
            return

        files = [f for f in session_dir.rglob("*") if f.is_file()]
        if len(files) >= MAX_FILES_PER_SESSION:
            raise PermissionError(
                f"File limit reached: {MAX_FILES_PER_SESSION} files per session"
            )

        total_bytes = sum(f.stat().st_size for f in files)
        if total_bytes >= MAX_TOTAL_BYTES_PER_SESSION:
            raise PermissionError(
                f"Storage limit reached: {MAX_TOTAL_BYTES_PER_SESSION} bytes per session"
            )

    async def execute(self, session_id: str, tool_name: str, args: dict) -> dict:
        handlers = {
            "fs_read_file": self._read_file,
            "fs_write_file": self._write_file,
            "fs_list_files": self._list_files,
        }
        handler = handlers.get(tool_name)
        if not handler:
            return {"error": f"Unknown tool: {tool_name}", "success": False}
        try:
            result = await handler(session_id, **args)
            return {"result": result, "success": True}
        except PermissionError as e:
            return {"error": str(e), "success": False}
        except Exception as e:
            return {"error": str(e), "success": False}

    async def _read_file(self, session_id: str, filepath: str) -> str:
        path = self._resolve(session_id, filepath)
        if not path.exists():
            return "File not found."

        # limit read size
        size = path.stat().st_size
        if size > MAX_FILE_SIZE_BYTES:
            return f"File too large to read ({size} bytes, max {MAX_FILE_SIZE_BYTES})."

        content = path.read_text(encoding="utf-8")

        # limit path depth in filenames
        rel = path.relative_to(self.base_dir / session_id)
        if len(rel.parts) > MAX_FILE_PATH_DEPTH:
            return f"Path depth exceeds limit ({MAX_FILE_PATH_DEPTH} levels)."

        return content

    async def _write_file(
        self, session_id: str, filepath: str, content: str, mode: str = "write"
    ) -> str:
        # validate content size
        if len(content.encode("utf-8")) > MAX_FILE_SIZE_BYTES:
            return f"Content too large ({len(content.encode('utf-8'))} bytes, max {MAX_FILE_SIZE_BYTES})."

        # reject binary-looking content
        if b"\x00" in content.encode("utf-8"):
            return "Binary content is not allowed."

        path = self._resolve(session_id, filepath)

        # limit path depth
        rel = path.relative_to(self.base_dir / session_id)
        if len(rel.parts) > MAX_FILE_PATH_DEPTH:
            return f"Path depth exceeds limit ({MAX_FILE_PATH_DEPTH} levels)."

        # check session limits before writing
        self._check_session_limits(session_id)

        path.parent.mkdir(parents=True, exist_ok=True)

        if mode == "append":
            existing_size = path.stat().st_size if path.exists() else 0
            if existing_size + len(content.encode("utf-8")) > MAX_FILE_SIZE_BYTES:
                return "Append would exceed file size limit."
            with open(path, "a", encoding="utf-8") as f:
                f.write(content + "\n")
        else:
            path.write_text(content, encoding="utf-8")
        return f"Written to {filepath}"

    async def _list_files(self, session_id: str, directory: str = ".") -> str:
        path = self._resolve(session_id, directory)
        if not path.exists():
            return "Directory not found."
        entries = []
        for item in sorted(path.iterdir()):
            prefix = "[DIR]" if item.is_dir() else "[FILE]"
            size = item.stat().st_size if item.is_file() else 0
            entries.append(f"{prefix} {item.name}" + (f" ({size}b)" if size else ""))
        return "\n".join(entries) if entries else "(empty directory)"
