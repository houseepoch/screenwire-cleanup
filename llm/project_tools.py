"""Project-scoped client-side tools for Grok agent runners."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path
from typing import Any


_TEXT_EXTENSIONS = {
    ".md",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".csv",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".html",
    ".css",
    ".sh",
    ".log",
}

_WRITE_ONLY_PROJECT_PREFIXES = (
    "creative_output/",
    "logs/",
    "dispatch/",
    "graph/",
    "frames/",
    "video/",
    "audio/",
    "assets/",
    "cast/",
    "locations/",
    "props/",
)

_SHELL_BLOCKLIST = (
    " rm ",
    " rm\n",
    "mv ",
    "chmod ",
    "chown ",
    "sudo ",
    "curl ",
    "wget ",
    "scp ",
    "ssh ",
    "git reset",
    "git checkout",
    "git clean",
    "dd ",
    "mkfs",
    ":(){",
    "shutdown",
    "reboot",
)


def build_project_tools() -> list[dict[str, Any]]:
    """Return JSON-schema tool specs for project-scoped agent execution."""
    return [
        {
            "type": "function",
            "name": "list_directory",
            "description": (
                "List files and subdirectories relative to the project root. "
                "You may also inspect repository support folders like agent_prompts/."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path. Defaults to '.'",
                    }
                },
            },
        },
        {
            "type": "function",
            "name": "read_file",
            "description": (
                "Read a text-based file relative to the project root or repo root. "
                "For PDFs, use read_pdf instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer", "minimum": 1},
                    "end_line": {"type": "integer", "minimum": 1},
                    "max_chars": {"type": "integer", "minimum": 1},
                },
                "required": ["path"],
            },
        },
        {
            "type": "function",
            "name": "read_pdf",
            "description": (
                "Extract text from a PDF in source_files/. Use repeated calls over page "
                "ranges to read the full document."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_page": {"type": "integer", "minimum": 1},
                    "end_page": {"type": "integer", "minimum": 1},
                    "max_chars": {"type": "integer", "minimum": 1},
                },
                "required": ["path"],
            },
        },
        {
            "type": "function",
            "name": "write_file",
            "description": (
                "Write or overwrite a file inside the project directory. Creates parent "
                "directories when needed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
        {
            "type": "function",
            "name": "append_file",
            "description": "Append text to a file inside the project directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
        {
            "type": "function",
            "name": "run_shell_command",
            "description": (
                "Run a safe read-oriented shell command inside the project directory. "
                "Use this for repo inspection or invoking the existing skill scripts. "
                "Destructive commands are blocked."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "cwd": {
                        "type": "string",
                        "description": "Relative working directory, defaults to '.'.",
                    },
                },
                "required": ["command"],
            },
        },
    ]


def make_project_tool_executor(
    *,
    project_root: Path,
    repo_root: Path,
    skills_dir: Path,
) -> Any:
    """Create a callable that executes project-scoped tool requests."""
    project_root = project_root.resolve()
    repo_root = repo_root.resolve()
    skills_dir = skills_dir.resolve()

    def _resolve_read_path(raw_path: str) -> Path:
        if not raw_path:
            raise ValueError("path is required")
        candidate = Path(raw_path).expanduser()
        if candidate.is_absolute():
            resolved = candidate.resolve()
            if _is_within(resolved, project_root) or _is_within(resolved, repo_root):
                return resolved
            raise ValueError(f"path escapes allowed roots: {raw_path}")

        direct_project = (project_root / candidate).resolve()
        if _is_within(direct_project, project_root) and direct_project.exists():
            return direct_project

        direct_repo = (repo_root / candidate).resolve()
        if _is_within(direct_repo, repo_root) and direct_repo.exists():
            return direct_repo

        if str(candidate).startswith(("agent_prompts/", "skills/", "llm/")):
            if _is_within(direct_repo, repo_root):
                return direct_repo

        if _is_within(direct_project, project_root):
            return direct_project
        raise ValueError(f"unresolvable path: {raw_path}")

    def _resolve_write_path(raw_path: str) -> Path:
        if not raw_path:
            raise ValueError("path is required")
        candidate = Path(raw_path)
        if candidate.is_absolute():
            resolved = candidate.resolve()
            if _is_within(resolved, project_root):
                return resolved
            raise ValueError(f"write path escapes project root: {raw_path}")

        normalized = candidate.as_posix().lstrip("./")
        if normalized and not normalized.startswith(_WRITE_ONLY_PROJECT_PREFIXES):
            raise ValueError(
                "writes are only allowed inside project output folders: "
                f"{normalized}"
            )
        resolved = (project_root / normalized).resolve()
        if not _is_within(resolved, project_root):
            raise ValueError(f"write path escapes project root: {raw_path}")
        return resolved

    def _resolve_cwd(raw_path: str | None) -> Path:
        if not raw_path:
            return project_root
        path = _resolve_read_path(raw_path)
        if path.is_file():
            return path.parent
        return path

    def _list_directory(path: str = ".") -> str:
        target = _resolve_cwd(path)
        if not target.exists():
            raise FileNotFoundError(f"directory not found: {path}")
        if not target.is_dir():
            raise NotADirectoryError(f"not a directory: {path}")
        rows: list[str] = [f"Directory: {target}"]
        for child in sorted(target.iterdir(), key=lambda item: (item.is_file(), item.name.lower())):
            kind = "dir" if child.is_dir() else "file"
            try:
                rel = child.relative_to(project_root)
            except ValueError:
                rel = child.relative_to(repo_root)
            size = child.stat().st_size if child.is_file() else 0
            rows.append(f"{kind}\t{rel.as_posix()}\t{size}")
        return "\n".join(rows)

    def _read_text_file(path: str, start_line: int | None = None, end_line: int | None = None, max_chars: int | None = None) -> str:
        target = _resolve_read_path(path)
        if target.suffix.lower() == ".pdf":
            raise ValueError("Use read_pdf for PDF documents")
        if not target.exists():
            raise FileNotFoundError(path)
        text = target.read_text(encoding="utf-8", errors="replace")
        if start_line or end_line:
            lines = text.splitlines()
            start_idx = max((start_line or 1) - 1, 0)
            end_idx = end_line if end_line is not None else len(lines)
            text = "\n".join(lines[start_idx:end_idx])
        limit = max_chars or 40000
        truncated = len(text) > limit
        if truncated:
            text = text[:limit]
        header = [f"Path: {target}"]
        if truncated:
            header.append(
                f"NOTE: output truncated to {limit} chars; narrow the request with line ranges if needed."
            )
        return "\n".join(header) + "\n\n" + text

    def _read_pdf(path: str, start_page: int | None = None, end_page: int | None = None, max_chars: int | None = None) -> str:
        target = _resolve_read_path(path)
        if target.suffix.lower() != ".pdf":
            raise ValueError("read_pdf only supports .pdf files")
        text, page_count = _extract_pdf_text(
            target,
            start_page=start_page or 1,
            end_page=end_page,
        )
        limit = max_chars or 50000
        truncated = len(text) > limit
        if truncated:
            text = text[:limit]
        header = [
            f"Path: {target}",
            f"PDF_PAGE_COUNT: {page_count}",
            f"PAGES_EXTRACTED: {start_page or 1}-{end_page or page_count}",
        ]
        if truncated:
            header.append(
                f"NOTE: text truncated to {limit} chars; call read_pdf again with a smaller page range."
            )
        return "\n".join(header) + "\n\n" + text

    def _write_file(path: str, content: str) -> str:
        target = _resolve_write_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"WROTE {target} ({len(content)} chars)"

    def _append_file(path: str, content: str) -> str:
        target = _resolve_write_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(content)
        return f"APPENDED {target} ({len(content)} chars)"

    def _run_shell_command(command: str, cwd: str | None = None) -> str:
        normalized = f" {command.strip()} "
        lowered = normalized.lower()
        if any(token in lowered for token in _SHELL_BLOCKLIST):
            raise ValueError(f"blocked shell command: {command}")
        workdir = _resolve_cwd(cwd)
        env = {
            **os.environ,
            "PROJECT_DIR": str(project_root),
            "SKILLS_DIR": str(skills_dir),
        }
        proc = subprocess.run(
            command,
            cwd=workdir,
            env=env,
            shell=True,
            text=True,
            capture_output=True,
            timeout=30,
        )
        payload = {
            "cwd": str(workdir),
            "returncode": proc.returncode,
            "stdout": proc.stdout[-12000:],
            "stderr": proc.stderr[-12000:],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    tool_map = {
        "list_directory": lambda **kwargs: _list_directory(kwargs.get("path", ".")),
        "read_file": lambda **kwargs: _read_text_file(
            kwargs["path"],
            start_line=kwargs.get("start_line"),
            end_line=kwargs.get("end_line"),
            max_chars=kwargs.get("max_chars"),
        ),
        "read_pdf": lambda **kwargs: _read_pdf(
            kwargs["path"],
            start_page=kwargs.get("start_page"),
            end_page=kwargs.get("end_page"),
            max_chars=kwargs.get("max_chars"),
        ),
        "write_file": lambda **kwargs: _write_file(kwargs["path"], kwargs["content"]),
        "append_file": lambda **kwargs: _append_file(kwargs["path"], kwargs["content"]),
        "run_shell_command": lambda **kwargs: _run_shell_command(
            kwargs["command"],
            cwd=kwargs.get("cwd"),
        ),
    }

    def _execute(name: str, arguments_json: str) -> str:
        if name not in tool_map:
            raise ValueError(f"unknown tool: {name}")
        try:
            arguments = json.loads(arguments_json) if arguments_json else {}
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid tool arguments: {exc}") from exc
        result = tool_map[name](**arguments)
        if isinstance(result, str):
            return result
        return json.dumps(result, ensure_ascii=False)

    return _execute


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _extract_pdf_text(path: Path, *, start_page: int, end_page: int | None) -> tuple[str, int]:
    page_count = _pdf_page_count(path)
    last_page = min(end_page or page_count, page_count)
    first_page = max(start_page, 1)
    if last_page < first_page:
        raise ValueError("end_page must be >= start_page")

    pdftotext = shutil_which("pdftotext")
    if pdftotext:
        proc = subprocess.run(
            [
                pdftotext,
                "-f",
                str(first_page),
                "-l",
                str(last_page),
                "-layout",
                "-nopgbrk",
                str(path),
                "-",
            ],
            text=True,
            capture_output=True,
            timeout=60,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout, page_count

    try:
        import fitz  # type: ignore
    except Exception as exc:  # pragma: no cover - environment-dependent
        raise RuntimeError("No PDF extraction backend available") from exc

    doc = fitz.open(path)
    try:
        chunks: list[str] = []
        for page_num in range(first_page - 1, last_page):
            chunks.append(doc.load_page(page_num).get_text("text"))
        return "\n".join(chunks), page_count
    finally:
        doc.close()


def _pdf_page_count(path: Path) -> int:
    try:
        import fitz  # type: ignore
    except Exception:
        return 1
    doc = fitz.open(path)
    try:
        return len(doc)
    finally:
        doc.close()


def shutil_which(name: str) -> str | None:
    """Local wrapper to avoid importing shutil at module import time."""
    import shutil

    return shutil.which(name)
