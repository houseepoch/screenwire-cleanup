"""Project-scoped client-side tools for Grok agent runners."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any

import httpx

from graph.api import get_frame_context, query_graph
from graph.store import GraphStore
from workspace_api import create_graph_node, delete_graph_node, get_graph_node, mark_project_file_change, patch_graph_node


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
            "name": "query_graph_database",
            "description": (
                "Query the project's narrative graph like a database using node type and exact-match filters."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "node_type": {"type": "string"},
                    "filters": {"type": "object"},
                    "max_results": {"type": "integer", "minimum": 1},
                },
                "required": ["node_type"],
            },
        },
        {
            "type": "function",
            "name": "get_frame_context",
            "description": (
                "Load the full context packet for a frame, including scene, dialogue, cast states, prop states, and location state."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "frame_id": {"type": "string"},
                },
                "required": ["frame_id"],
            },
        },
        {
            "type": "function",
            "name": "get_graph_node",
            "description": (
                "Read a structured graph node by type and id from the project's narrative graph. "
                "Use this when the user is focused on a cast member, location, prop, scene, or frame."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "node_type": {"type": "string"},
                    "node_id": {"type": "string"},
                },
                "required": ["node_type", "node_id"],
            },
        },
        {
            "type": "function",
            "name": "create_graph_node",
            "description": (
                "Create a structured cast, location, or prop node in the project's narrative graph. "
                "Use this when the user wants to add a new story entity."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "node_type": {"type": "string"},
                    "data": {"type": "object"},
                },
                "required": ["node_type", "data"],
            },
        },
        {
            "type": "function",
            "name": "update_graph_node",
            "description": (
                "Patch a structured graph node in the project's narrative graph. "
                "Use this for targeted cast/location/prop/scene/frame edits instead of loose text rewrites."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "node_type": {"type": "string"},
                    "node_id": {"type": "string"},
                    "updates": {"type": "object"},
                },
                "required": ["node_type", "node_id", "updates"],
            },
        },
        {
            "type": "function",
            "name": "delete_graph_node",
            "description": (
                "Delete a cast, location, or prop node from the project's narrative graph. "
                "Use this when the user explicitly wants to remove an entity."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "node_type": {"type": "string"},
                    "node_id": {"type": "string"},
                },
                "required": ["node_type", "node_id"],
            },
        },
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
        {
            "type": "function",
            "name": "grep_project_research",
            "description": (
                "Search project and support files for text matches. Use this for fast research across prompts, reports, logs, and creative output."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string"},
                    "max_results": {"type": "integer", "minimum": 1},
                    "case_sensitive": {"type": "boolean"},
                },
                "required": ["pattern"],
            },
        },
        {
            "type": "function",
            "name": "generate_image_with_nanobanana",
            "description": (
                "Generate a new image through the Nano Banana chain with optional reference images."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "output_path": {"type": "string"},
                    "size": {"type": "string"},
                    "seed": {"type": "integer"},
                    "reference_images": {"type": "array", "items": {"type": "string"}},
                    "image_search": {"type": "boolean"},
                    "google_search": {"type": "boolean"},
                },
                "required": ["prompt", "output_path"],
            },
        },
        {
            "type": "function",
            "name": "edit_image_with_nanobanana",
            "description": (
                "Edit an existing image through the Nano Banana edit chain."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "input_path": {"type": "string"},
                    "prompt": {"type": "string"},
                    "output_path": {"type": "string"},
                    "size": {"type": "string"},
                    "seed": {"type": "integer"},
                    "image_search": {"type": "boolean"},
                    "google_search": {"type": "boolean"},
                },
                "required": ["input_path", "prompt", "output_path"],
            },
        },
        {
            "type": "function",
            "name": "generate_video_with_grok",
            "description": "Generate a video clip from a frame image using Grok video on Replicate.",
            "parameters": {
                "type": "object",
                "properties": {
                    "image_path": {"type": "string"},
                    "prompt": {"type": "string"},
                    "output_path": {"type": "string"},
                    "dialogue_text": {"type": "string"},
                    "duration": {"type": "integer", "minimum": 1, "maximum": 15},
                    "frame_id": {"type": "string"},
                },
                "required": ["image_path", "prompt", "output_path"],
            },
        },
        {
            "type": "function",
            "name": "extend_video_with_grok",
            "description": "Extend an existing video clip using Grok video extension on Replicate.",
            "parameters": {
                "type": "object",
                "properties": {
                    "video_path": {"type": "string"},
                    "output_path": {"type": "string"},
                    "prompt": {"type": "string"},
                    "duration": {"type": "integer", "minimum": 1, "maximum": 15},
                },
                "required": ["video_path", "output_path"],
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

    def _backend_base_url() -> str:
        port = os.environ.get("SW_PORT", "8000").strip() or "8000"
        return f"http://127.0.0.1:{port}"

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
        try:
            rel_path = target.relative_to(project_root).as_posix()
        except ValueError:
            rel_path = path
        mark_project_file_change(project_root, rel_path, source="morpheus_write_file")
        return f"WROTE {target} ({len(content)} chars)"

    def _append_file(path: str, content: str) -> str:
        target = _resolve_write_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(content)
        try:
            rel_path = target.relative_to(project_root).as_posix()
        except ValueError:
            rel_path = path
        mark_project_file_change(project_root, rel_path, source="morpheus_append_file")
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

    def _query_graph_database(
        node_type: str,
        filters: dict[str, Any] | None = None,
        max_results: int | None = None,
    ) -> str:
        store = GraphStore(project_root)
        if not store.exists():
            raise FileNotFoundError("narrative graph not found")
        graph = store.load()
        results = query_graph(graph, node_type, filters or None)
        if max_results:
            results = results[: max(1, int(max_results))]
        return json.dumps(results, ensure_ascii=False, indent=2)

    def _get_frame_context_tool(frame_id: str) -> str:
        store = GraphStore(project_root)
        if not store.exists():
            raise FileNotFoundError("narrative graph not found")
        graph = store.load()
        context = get_frame_context(graph, frame_id)
        return json.dumps(context, ensure_ascii=False, indent=2, default=str)

    def _grep_project_research(
        pattern: str,
        path: str = ".",
        max_results: int | None = None,
        case_sensitive: bool = False,
    ) -> str:
        if not pattern:
            raise ValueError("pattern is required")
        root = _resolve_cwd(path)
        flags = 0 if case_sensitive else re.IGNORECASE
        regex = re.compile(pattern, flags)
        matches: list[dict[str, Any]] = []
        limit = max(1, int(max_results or 20))

        def _iter_files(base: Path):
            if base.is_file():
                yield base
                return
            for child in sorted(base.rglob("*")):
                if child.is_file() and child.suffix.lower() in _TEXT_EXTENSIONS:
                    yield child

        for file_path in _iter_files(root):
            try:
                text = file_path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            for line_no, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    try:
                        rel = file_path.relative_to(project_root)
                    except ValueError:
                        rel = file_path.relative_to(repo_root)
                    matches.append(
                        {
                            "path": rel.as_posix(),
                            "line": line_no,
                            "text": line.strip(),
                        }
                    )
                    if len(matches) >= limit:
                        return json.dumps(matches, ensure_ascii=False, indent=2)
        return json.dumps(matches, ensure_ascii=False, indent=2)

    def _post_internal_api(route: str, payload: dict[str, Any], timeout: float = 600.0) -> str:
        response = httpx.post(f"{_backend_base_url()}{route}", json=payload, timeout=timeout)
        response.raise_for_status()
        return json.dumps(response.json(), ensure_ascii=False, indent=2)

    def _generate_image_with_nanobanana(
        prompt: str,
        output_path: str,
        size: str = "landscape_16_9",
        seed: int | None = None,
        reference_images: list[str] | None = None,
        image_search: bool = False,
        google_search: bool = False,
    ) -> str:
        resolved_output = _resolve_write_path(output_path)
        refs = [str(_resolve_read_path(ref)) for ref in (reference_images or [])]
        payload: dict[str, Any] = {
            "prompt": prompt,
            "size": size,
            "output_path": str(resolved_output),
            "output_format": resolved_output.suffix.lstrip(".") or "png",
            "reference_images": refs,
            "image_search": bool(image_search),
            "google_search": bool(google_search),
        }
        if seed is not None:
            payload["seed"] = int(seed)
        return _post_internal_api("/internal/fresh-generation", payload)

    def _edit_image_with_nanobanana(
        input_path: str,
        prompt: str,
        output_path: str,
        size: str = "landscape_16_9",
        seed: int | None = None,
        image_search: bool = False,
        google_search: bool = False,
    ) -> str:
        resolved_input = _resolve_read_path(input_path)
        resolved_output = _resolve_write_path(output_path)
        payload: dict[str, Any] = {
            "input_path": str(resolved_input),
            "prompt": prompt,
            "size": size,
            "output_path": str(resolved_output),
            "output_format": resolved_output.suffix.lstrip(".") or "png",
            "image_search": bool(image_search),
            "google_search": bool(google_search),
        }
        if seed is not None:
            payload["seed"] = int(seed)
        return _post_internal_api("/internal/edit-image", payload)

    def _generate_video_with_grok(
        image_path: str,
        prompt: str,
        output_path: str,
        dialogue_text: str = "",
        duration: int = 5,
        frame_id: str = "",
    ) -> str:
        resolved_image = _resolve_read_path(image_path)
        resolved_output = _resolve_write_path(output_path)
        payload: dict[str, Any] = {
            "image_path": str(resolved_image),
            "prompt": prompt,
            "dialogue_text": dialogue_text,
            "output_path": str(resolved_output),
            "duration": int(duration),
        }
        if frame_id:
            payload["frame_id"] = frame_id
        return _post_internal_api("/internal/generate-video", payload, timeout=1800.0)

    def _extend_video_with_grok(
        video_path: str,
        output_path: str,
        prompt: str = "",
        duration: int = 5,
    ) -> str:
        resolved_video = _resolve_read_path(video_path)
        resolved_output = _resolve_write_path(output_path)
        payload = {
            "video_path": str(resolved_video),
            "output_path": str(resolved_output),
            "prompt": prompt,
            "duration": int(duration),
        }
        return _post_internal_api("/internal/extend-video", payload, timeout=1800.0)

    def _get_graph_node(node_type: str, node_id: str) -> str:
        node = get_graph_node(project_root, node_type, node_id)
        if node is None:
            raise FileNotFoundError(f"graph node not found: {node_type}:{node_id}")
        return json.dumps(node, ensure_ascii=False, indent=2)

    def _update_graph_node(node_type: str, node_id: str, updates: dict[str, Any]) -> str:
        patched = patch_graph_node(project_root, node_type, node_id, updates, modified_by="morpheus_apply_mode")
        return json.dumps(patched, ensure_ascii=False, indent=2)

    def _create_graph_node(node_type: str, data: dict[str, Any]) -> str:
        created = create_graph_node(project_root, node_type, data, modified_by="morpheus_apply_mode")
        return json.dumps(created, ensure_ascii=False, indent=2)

    def _delete_graph_node(node_type: str, node_id: str) -> str:
        result = delete_graph_node(project_root, node_type, node_id, modified_by="morpheus_apply_mode")
        return json.dumps(result, ensure_ascii=False, indent=2)

    tool_map = {
        "query_graph_database": lambda **kwargs: _query_graph_database(
            kwargs["node_type"],
            filters=kwargs.get("filters"),
            max_results=kwargs.get("max_results"),
        ),
        "get_frame_context": lambda **kwargs: _get_frame_context_tool(kwargs["frame_id"]),
        "get_graph_node": lambda **kwargs: _get_graph_node(kwargs["node_type"], kwargs["node_id"]),
        "create_graph_node": lambda **kwargs: _create_graph_node(
            kwargs["node_type"],
            kwargs["data"],
        ),
        "update_graph_node": lambda **kwargs: _update_graph_node(
            kwargs["node_type"],
            kwargs["node_id"],
            kwargs["updates"],
        ),
        "delete_graph_node": lambda **kwargs: _delete_graph_node(
            kwargs["node_type"],
            kwargs["node_id"],
        ),
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
        "grep_project_research": lambda **kwargs: _grep_project_research(
            kwargs["pattern"],
            path=kwargs.get("path", "."),
            max_results=kwargs.get("max_results"),
            case_sensitive=bool(kwargs.get("case_sensitive", False)),
        ),
        "generate_image_with_nanobanana": lambda **kwargs: _generate_image_with_nanobanana(
            kwargs["prompt"],
            kwargs["output_path"],
            size=kwargs.get("size", "landscape_16_9"),
            seed=kwargs.get("seed"),
            reference_images=kwargs.get("reference_images") or [],
            image_search=bool(kwargs.get("image_search", False)),
            google_search=bool(kwargs.get("google_search", False)),
        ),
        "edit_image_with_nanobanana": lambda **kwargs: _edit_image_with_nanobanana(
            kwargs["input_path"],
            kwargs["prompt"],
            kwargs["output_path"],
            size=kwargs.get("size", "landscape_16_9"),
            seed=kwargs.get("seed"),
            image_search=bool(kwargs.get("image_search", False)),
            google_search=bool(kwargs.get("google_search", False)),
        ),
        "generate_video_with_grok": lambda **kwargs: _generate_video_with_grok(
            kwargs["image_path"],
            kwargs["prompt"],
            kwargs["output_path"],
            dialogue_text=kwargs.get("dialogue_text", ""),
            duration=kwargs.get("duration", 5),
            frame_id=kwargs.get("frame_id", ""),
        ),
        "extend_video_with_grok": lambda **kwargs: _extend_video_with_grok(
            kwargs["video_path"],
            kwargs["output_path"],
            prompt=kwargs.get("prompt", ""),
            duration=kwargs.get("duration", 5),
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
