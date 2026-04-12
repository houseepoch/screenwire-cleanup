#!/usr/bin/env python3
"""
Generate the canonical API reference from source fragments under
AGENT_READ_HERE_FIRST/.

The source of truth is intentionally constrained to one directory so agents can
keep API research notes close together while the repo still exposes a single
canonical API_REFERENCE.md file at the root.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

SOURCE_GLOBS = ("API_*.md", "api_*.md", "replicate_*.md")
DEFAULT_SOURCE_DIR = Path("AGENT_READ_HERE_FIRST")
DEFAULT_OUTPUT = Path("API_REFERENCE.md")


def _matches_source_name(name: str) -> bool:
    lower = name.lower()
    return (
        lower.startswith("api_") and lower.endswith(".md")
    ) or (
        lower.startswith("replicate_") and lower.endswith(".md")
    )


def _source_priority(path: Path) -> tuple[int, str]:
    name = path.name.lower()
    if name == "api_tool_reference.md":
        return (0, name)
    if name.startswith("api_"):
        return (1, name)
    if name.startswith("replicate_"):
        return (2, name)
    return (99, name)


def discover_source_files(source_dir: Path) -> list[Path]:
    """Return top-level source fragments from the configured source directory."""
    if not source_dir.exists():
        return []
    return sorted(
        [
            path
            for path in source_dir.iterdir()
            if path.is_file() and _matches_source_name(path.name)
        ],
        key=_source_priority,
    )


def _extract_heading_and_body(text: str, fallback_title: str) -> tuple[str, str]:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("# "):
            title = stripped[2:].strip() or fallback_title
            body = "\n".join(lines[index + 1 :]).strip()
            return title, body
        break
    return fallback_title, text.strip()


def _display_path(source_dir: Path, path: Path) -> str:
    try:
        return path.relative_to(source_dir.parent).as_posix()
    except ValueError:
        return path.as_posix()


def render_api_reference(source_dir: Path, sources: list[Path]) -> str:
    rel_source_dir = source_dir.as_posix()
    lines = [
        "# ScreenWire AI - API Reference",
        "",
        "> Generated file. Do not hand-edit this document.",
        f"> Rebuild with `python3 build_api_reference.py`. Source fragments are loaded only from `{rel_source_dir}/`.",
        "",
        "## Source Fragments",
    ]

    if sources:
        for source in sources:
            lines.append(f"- `{_display_path(source_dir, source)}`")
    else:
        lines.append(f"- No source fragments found under `{rel_source_dir}/`.")

    for source in sources:
        title, body = _extract_heading_and_body(
            source.read_text(encoding="utf-8"),
            fallback_title=source.stem.replace("_", " "),
        )
        lines.extend(
            [
                "",
                "---",
                "",
                f"## {title}",
                "",
                f"_Source: `{_display_path(source_dir, source)}`_",
            ]
        )
        if body:
            lines.extend(["", body.rstrip()])

    return "\n".join(lines).rstrip() + "\n"


def write_api_reference(output_path: Path, content: str) -> bool:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    previous = output_path.read_text(encoding="utf-8") if output_path.exists() else None
    if previous == content:
        return False
    output_path.write_text(content, encoding="utf-8")
    return True


def build_api_reference(source_dir: Path, output_path: Path) -> tuple[bool, list[Path]]:
    sources = discover_source_files(source_dir)
    content = render_api_reference(source_dir, sources)
    changed = write_api_reference(output_path, content)
    return changed, sources


def _watchdog_watch(source_dir: Path, output_path: Path, poll_interval: float) -> int:
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
    except ImportError:
        return _poll_watch(source_dir, output_path, poll_interval)

    class _Handler(FileSystemEventHandler):
        def __init__(self) -> None:
            self._last_build = 0.0

        def on_any_event(self, event) -> None:  # type: ignore[override]
            if getattr(event, "is_directory", False):
                return
            path_str = getattr(event, "dest_path", None) or getattr(event, "src_path", "")
            path = Path(path_str)
            if path.parent.resolve() != source_dir.resolve():
                return
            if not _matches_source_name(path.name) and path.suffix.lower() != ".md":
                return

            now = time.monotonic()
            if now - self._last_build < 0.2:
                return
            self._last_build = now

            changed, sources = build_api_reference(source_dir, output_path)
            status = "updated" if changed else "unchanged"
            print(f"[api-reference] {status} ({len(sources)} source files)")

    changed, sources = build_api_reference(source_dir, output_path)
    status = "updated" if changed else "unchanged"
    print(f"[api-reference] {status} ({len(sources)} source files)")

    observer = Observer()
    observer.schedule(_Handler(), str(source_dir), recursive=False)
    observer.start()
    print(f"[api-reference] watching {source_dir} -> {output_path}")
    try:
        while True:
            time.sleep(poll_interval)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
    return 0


def _snapshot(source_dir: Path) -> dict[str, tuple[int, int]]:
    snap: dict[str, tuple[int, int]] = {}
    for path in discover_source_files(source_dir):
        stat = path.stat()
        snap[path.name] = (stat.st_mtime_ns, stat.st_size)
    return snap


def _poll_watch(source_dir: Path, output_path: Path, poll_interval: float) -> int:
    changed, sources = build_api_reference(source_dir, output_path)
    status = "updated" if changed else "unchanged"
    print(f"[api-reference] {status} ({len(sources)} source files)")
    print(f"[api-reference] polling {source_dir} every {poll_interval:.1f}s")

    last_snapshot = _snapshot(source_dir)
    try:
        while True:
            time.sleep(poll_interval)
            current = _snapshot(source_dir)
            if current == last_snapshot:
                continue
            last_snapshot = current
            changed, sources = build_api_reference(source_dir, output_path)
            status = "updated" if changed else "unchanged"
            print(f"[api-reference] {status} ({len(sources)} source files)")
    except KeyboardInterrupt:
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=DEFAULT_SOURCE_DIR,
        help="Directory containing API source fragments.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Destination markdown file.",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Watch the source directory and rebuild on matching file changes.",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=1.0,
        help="Sleep interval for watch mode. Used directly for polling and as the observer heartbeat.",
    )
    args = parser.parse_args(argv)

    if args.watch:
        return _watchdog_watch(args.source_dir, args.output, args.poll_interval)

    changed, sources = build_api_reference(args.source_dir, args.output)
    status = "updated" if changed else "unchanged"
    print(f"[api-reference] {status} ({len(sources)} source files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
