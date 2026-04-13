#!/usr/bin/env python3
"""
Build repo snapshot and Python dependency architecture reports.

Outputs are written into docs/Architecture/ and previous generated outputs are
rotated into docs/Architecture/0_archived/ before new files are written.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import os
import re
import shutil
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

DEFAULT_OUTPUT_DIR = Path("docs/Architecture")
DEFAULT_ARCHIVE_DIR = DEFAULT_OUTPUT_DIR / "0_archived"
STATIC_OUTPUT_FILE_NAMES = (
    "00_architecture_summary.md",
    "10_repo_snapshot.md",
    "20_python_dependency_report.md",
    "21_python_dependency_graph.mmd",
)
SNAPSHOT_FILE_PREFIX = "10_repo_snapshot_part_"
DEFAULT_SNAPSHOT_MAX_BYTES = 25 * 1024 * 1024
PRESERVED_ARCHITECTURE_FILES = {
    "README.md",
    "run_architecture_reports.sh",
    "run_architecture_reports.bat",
}
EXCLUDED_TOP_LEVEL_DIR_NAMES = {
    "docs",
}
EXCLUDED_DIR_NAMES = {
    ".git",
    ".idea",
    ".pytest_cache",
    ".qodo",
    ".venv",
    "__pycache__",
    "node_modules",
    "projects",
    "venv",
}
EXCLUDED_FILE_NAMES = {
    ".env",
}
CODE_FENCE_LANG_BY_SUFFIX = {
    ".md": "md",
    ".py": "python",
    ".json": "json",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".toml": "toml",
    ".sh": "bash",
    ".bash": "bash",
    ".txt": "text",
    ".xml": "xml",
    ".html": "html",
    ".css": "css",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".jsx": "jsx",
    ".csv": "csv",
    ".env": "dotenv",
}
BINARY_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".mp4",
    ".mov",
    ".avi",
    ".mkv",
    ".pdf",
    ".pyc",
    ".zip",
    ".gz",
    ".tar",
    ".woff",
    ".woff2",
}
ENV_SECRET_LINE_RE = re.compile(
    r"^([A-Za-z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)[A-Za-z0-9_]*\s*=\s*).*$",
    re.MULTILINE,
)
GENERIC_SECRET_PATTERNS = (
    re.compile(r"(xai-)[A-Za-z0-9_-]{12,}"),
    re.compile(r"(sk_)[A-Za-z0-9]{12,}"),
    re.compile(r"(r8_)[A-Za-z0-9]{12,}"),
)


@dataclass(frozen=True)
class FileEntry:
    path: Path
    relative_path: str
    size_bytes: int
    is_binary: bool


@dataclass(frozen=True)
class ModuleInfo:
    module_name: str
    path: Path
    relative_path: str
    is_package: bool


@dataclass(frozen=True)
class DependencyEdge:
    source_module: str
    target_module: str


@dataclass
class DependencyAnalysis:
    modules: dict[str, ModuleInfo]
    internal_edges: set[DependencyEdge]
    external_imports: Counter[str]
    stdlib_imports: Counter[str]


@dataclass(frozen=True)
class SnapshotPart:
    file_name: str
    content: str


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utc_now().isoformat()


def timestamp_slug() -> str:
    return utc_now().strftime("%Y%m%dT%H%M%SZ")


def is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def should_exclude_dir(path: Path, *, root: Path, output_dir: Path) -> bool:
    if path.name in EXCLUDED_DIR_NAMES:
        return True
    try:
        relative = path.relative_to(root)
    except ValueError:
        relative = None
    if relative and relative.parts and relative.parts[0] in EXCLUDED_TOP_LEVEL_DIR_NAMES:
        return True
    return is_within(path, output_dir)


def looks_binary(path: Path) -> bool:
    if path.suffix.lower() in BINARY_SUFFIXES:
        return True
    try:
        with path.open("rb") as handle:
            chunk = handle.read(4096)
    except OSError:
        return False
    if b"\x00" in chunk:
        return True
    try:
        chunk.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def collect_repo_files(root: Path, *, output_dir: Path) -> list[FileEntry]:
    entries: list[FileEntry] = []
    for current_root, dir_names, file_names in os.walk(root):
        current_path = Path(current_root)
        dir_names[:] = sorted(
            name
            for name in dir_names
            if not should_exclude_dir(current_path / name, root=root, output_dir=output_dir)
        )
        for file_name in sorted(file_names):
            path = current_path / file_name
            if file_name in EXCLUDED_FILE_NAMES:
                continue
            if is_within(path, output_dir):
                continue
            if should_exclude_dir(path.parent, root=root, output_dir=output_dir):
                continue
            stat = path.stat()
            entries.append(
                FileEntry(
                    path=path,
                    relative_path=path.relative_to(root).as_posix(),
                    size_bytes=stat.st_size,
                    is_binary=looks_binary(path),
                )
            )
    return sorted(entries, key=lambda entry: entry.relative_path)


def read_text_file(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def sanitize_text_for_snapshot(path: Path, text: str) -> str:
    sanitized = text
    if path.name == ".env" or path.name.startswith(".env."):
        sanitized = re.sub(ENV_SECRET_LINE_RE, r"\1[REDACTED]", sanitized)
    else:
        sanitized = re.sub(ENV_SECRET_LINE_RE, r"\1[REDACTED]", sanitized)

    for pattern in GENERIC_SECRET_PATTERNS:
        sanitized = pattern.sub(r"\1[REDACTED]", sanitized)
    return sanitized


def sha256_hex(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def module_name_for_path(root: Path, path: Path) -> str:
    relative = path.relative_to(root)
    parts = list(relative.parts)
    parts[-1] = Path(parts[-1]).stem
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(part for part in parts if part)


def collect_python_modules(root: Path, *, output_dir: Path) -> dict[str, ModuleInfo]:
    modules: dict[str, ModuleInfo] = {}
    for entry in collect_repo_files(root, output_dir=output_dir):
        if entry.path.suffix != ".py":
            continue
        module_name = module_name_for_path(root, entry.path)
        if not module_name:
            continue
        modules[module_name] = ModuleInfo(
            module_name=module_name,
            path=entry.path,
            relative_path=entry.relative_path,
            is_package=entry.path.name == "__init__.py",
        )
    return modules


def _source_package(source: ModuleInfo) -> str:
    if source.is_package:
        return source.module_name
    if "." in source.module_name:
        return source.module_name.rsplit(".", 1)[0]
    return ""


def _resolve_relative_import(source: ModuleInfo, level: int, module: str | None) -> str:
    if level == 0:
        return module or ""

    package = _source_package(source)
    package_parts = package.split(".") if package else []
    keep = max(len(package_parts) - (level - 1), 0)
    package_parts = package_parts[:keep]
    if module:
        package_parts.extend(module.split("."))
    return ".".join(part for part in package_parts if part)


def _longest_local_prefix(candidate: str, modules: dict[str, ModuleInfo]) -> str | None:
    current = candidate
    while current:
        if current in modules:
            return current
        if "." not in current:
            break
        current = current.rsplit(".", 1)[0]
    return current if current in modules else None


def _candidate_targets_for_import(
    source: ModuleInfo,
    node: ast.AST,
    modules: dict[str, ModuleInfo],
) -> tuple[set[str], list[str]]:
    local_targets: set[str] = set()
    external_roots: list[str] = []

    if isinstance(node, ast.Import):
        for alias in node.names:
            resolved = _longest_local_prefix(alias.name, modules)
            if resolved:
                local_targets.add(resolved)
            else:
                external_roots.append(alias.name.split(".", 1)[0])
        return local_targets, external_roots

    if not isinstance(node, ast.ImportFrom):
        return local_targets, external_roots

    base_module = _resolve_relative_import(source, node.level, node.module)
    if base_module:
        resolved_base = _longest_local_prefix(base_module, modules)
        if resolved_base:
            local_targets.add(resolved_base)
        elif node.level == 0:
            external_roots.append(base_module.split(".", 1)[0])

    for alias in node.names:
        if alias.name == "*":
            continue
        nested = ".".join(part for part in [base_module, alias.name] if part)
        if nested:
            resolved_nested = _longest_local_prefix(nested, modules)
            if resolved_nested:
                local_targets.add(resolved_nested)
                continue
        if node.level == 0 and not base_module:
            resolved_alias = _longest_local_prefix(alias.name, modules)
            if resolved_alias:
                local_targets.add(resolved_alias)
            else:
                external_roots.append(alias.name.split(".", 1)[0])

    return local_targets, external_roots


def _is_standard_library_root(module_name: str) -> bool:
    if not module_name:
        return False
    stdlib_names = getattr(sys, "stdlib_module_names", frozenset())
    return module_name in stdlib_names or module_name in sys.builtin_module_names


def analyze_python_dependencies(root: Path, *, output_dir: Path) -> DependencyAnalysis:
    modules = collect_python_modules(root, output_dir=output_dir)
    internal_edges: set[DependencyEdge] = set()
    external_imports: Counter[str] = Counter()
    stdlib_imports: Counter[str] = Counter()

    for module in modules.values():
        try:
            tree = ast.parse(read_text_file(module.path), filename=str(module.path))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            local_targets, external_roots = _candidate_targets_for_import(module, node, modules)
            for target in local_targets:
                if target != module.module_name:
                    internal_edges.add(
                        DependencyEdge(source_module=module.module_name, target_module=target)
                    )
            for external_root in external_roots:
                if external_root:
                    if _is_standard_library_root(external_root):
                        stdlib_imports[external_root] += 1
                    else:
                        external_imports[external_root] += 1

    return DependencyAnalysis(
        modules=modules,
        internal_edges=internal_edges,
        external_imports=external_imports,
        stdlib_imports=stdlib_imports,
    )


def archive_existing_outputs(output_dir: Path, archive_dir: Path) -> list[Path]:
    def _is_generated_artifact(path: Path) -> bool:
        if path.name in STATIC_OUTPUT_FILE_NAMES:
            return True
        if path.name.startswith(SNAPSHOT_FILE_PREFIX) and path.suffix == ".md":
            return True
        return False

    existing_files = sorted(
        path
        for path in output_dir.rglob("*")
        if path.is_file()
        and not is_within(path, archive_dir)
        and path.name not in PRESERVED_ARCHITECTURE_FILES
        and _is_generated_artifact(path)
    )
    if not existing_files:
        return []

    archive_run_dir = archive_dir / timestamp_slug()
    archive_run_dir.mkdir(parents=True, exist_ok=True)

    archived: list[Path] = []
    for source in existing_files:
        relative = source.relative_to(output_dir)
        destination = archive_run_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
        archived.append(destination)
    return archived


def _fence_lang(path: Path) -> str:
    return CODE_FENCE_LANG_BY_SUFFIX.get(path.suffix.lower(), "")


def _utf8_len(value: str) -> int:
    return len(value.encode("utf-8"))


def _repo_snapshot_header(
    root: Path,
    files: list[FileEntry],
    *,
    part_number: int,
    total_parts: int,
    max_part_bytes: int,
) -> str:
    total_bytes = sum(entry.size_bytes for entry in files)
    text_files = sum(1 for entry in files if not entry.is_binary)
    binary_files = len(files) - text_files

    header_lines = [
        "# Repo Snapshot",
        "",
        f"- Generated at: `{iso_now()}`",
        f"- Repo root: `{root}`",
        f"- Part: `{part_number}` of `{total_parts}`",
        f"- Max part size: `{max_part_bytes}` bytes",
        f"- Included files: `{len(files)}`",
        f"- Text files embedded: `{text_files}`",
        f"- Binary files summarized: `{binary_files}`",
        f"- Total bytes scanned: `{total_bytes}`",
        "",
    ]
    return "\n".join(header_lines) + "\n"


def _snapshot_header_reserve(root: Path, files: list[FileEntry], max_part_bytes: int) -> int:
    sample = _repo_snapshot_header(
        root,
        files,
        part_number=9999,
        total_parts=9999,
        max_part_bytes=max_part_bytes,
    )
    return _utf8_len(sample)


def _split_text_to_byte_slices(text: str, max_bytes: int) -> list[str]:
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    if _utf8_len(text) <= max_bytes:
        return [text]

    parts: list[str] = []
    current_chars: list[str] = []
    current_bytes = 0
    for char in text:
        encoded_len = _utf8_len(char)
        if current_chars and current_bytes + encoded_len > max_bytes:
            parts.append("".join(current_chars))
            current_chars = [char]
            current_bytes = encoded_len
        else:
            current_chars.append(char)
            current_bytes += encoded_len
    if current_chars:
        parts.append("".join(current_chars))
    return parts


def _snapshot_sections_for_entry(entry: FileEntry, max_body_bytes: int) -> list[str]:
    heading = f"## `{entry.relative_path}`\n\n"
    if entry.is_binary:
        return [
            "".join(
                [
                    heading,
                    "- Type: binary\n",
                    f"- Size: `{entry.size_bytes}` bytes\n",
                    f"- SHA256: `{sha256_hex(entry.path)}`\n\n",
                ]
            )
        ]

    lang = _fence_lang(entry.path)
    fence = f"```{lang}" if lang else "```"
    text = sanitize_text_for_snapshot(entry.path, read_text_file(entry.path)).rstrip()
    full_section = f"{heading}{fence}\n{text}\n```\n\n"
    if _utf8_len(full_section) <= max_body_bytes:
        return [full_section]

    continued_heading_template = (
        f"## `{entry.relative_path}` (segment `9999` of `9999`)\n\n"
    )
    heading_budget = max(_utf8_len(heading), _utf8_len(continued_heading_template))
    available_chunk_bytes = max_body_bytes - heading_budget - _utf8_len(f"{fence}\n\n```\n\n")
    if available_chunk_bytes <= 0:
        raise ValueError(f"snapshot section budget too small for {entry.relative_path}")

    chunks = _split_text_to_byte_slices(text, available_chunk_bytes)
    total = len(chunks)
    sections: list[str] = []
    for index, chunk in enumerate(chunks, start=1):
        continued_heading = (
            f"## `{entry.relative_path}` (segment `{index}` of `{total}`)\n\n"
        )
        sections.append(f"{continued_heading}{fence}\n{chunk}\n```\n\n")
    return sections


def render_repo_snapshot_parts(
    root: Path,
    files: list[FileEntry],
    *,
    max_part_bytes: int,
) -> list[SnapshotPart]:
    header_reserve = _snapshot_header_reserve(root, files, max_part_bytes)
    max_body_bytes = max_part_bytes - header_reserve
    if max_body_bytes <= 0:
        raise ValueError("max_part_bytes is too small for the snapshot header")

    bodies: list[str] = []
    current_sections: list[str] = []
    current_bytes = 0

    for entry in files:
        for section in _snapshot_sections_for_entry(entry, max_body_bytes):
            section_bytes = _utf8_len(section)
            if current_sections and current_bytes + section_bytes > max_body_bytes:
                bodies.append("".join(current_sections))
                current_sections = [section]
                current_bytes = section_bytes
            else:
                current_sections.append(section)
                current_bytes += section_bytes

    if current_sections:
        bodies.append("".join(current_sections))

    total_parts = max(len(bodies), 1)
    rendered: list[SnapshotPart] = []
    for index, body in enumerate(bodies or [""], start=1):
        header = _repo_snapshot_header(
            root,
            files,
            part_number=index,
            total_parts=total_parts,
            max_part_bytes=max_part_bytes,
        )
        content = (header + body).rstrip() + "\n"
        rendered.append(
            SnapshotPart(
                file_name=f"{SNAPSHOT_FILE_PREFIX}{index:03d}.md",
                content=content,
            )
        )
    return rendered


def render_repo_snapshot_index(
    root: Path,
    files: list[FileEntry],
    parts: list[SnapshotPart],
    *,
    max_part_bytes: int,
) -> str:
    total_bytes = sum(entry.size_bytes for entry in files)
    text_files = sum(1 for entry in files if not entry.is_binary)
    binary_files = len(files) - text_files

    lines = [
        "# Repo Snapshot Index",
        "",
        f"- Generated at: `{iso_now()}`",
        f"- Repo root: `{root}`",
        f"- Included files: `{len(files)}`",
        f"- Text files embedded across parts: `{text_files}`",
        f"- Binary files summarized across parts: `{binary_files}`",
        f"- Total bytes scanned: `{total_bytes}`",
        f"- Snapshot part count: `{len(parts)}`",
        f"- Max snapshot part size: `{max_part_bytes}` bytes",
        "",
        "## Snapshot Parts",
        "",
    ]

    for index, part in enumerate(parts, start=1):
        part_size = len(part.content.encode("utf-8"))
        lines.append(f"- `{part.file_name}`: part `{index}` of `{len(parts)}`, `{part_size}` bytes")

    lines.extend(
        [
            "",
            "## Notes",
            "",
            f"- Open the split snapshot files named `{SNAPSHOT_FILE_PREFIX}NNN.md` for the actual concatenated repo contents.",
            "- This index exists so the snapshot surface still has one stable top-level document even when the actual content is sharded.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _top_level_directory(relative_path: str) -> str:
    return relative_path.split("/", 1)[0] if "/" in relative_path else "."


def render_dependency_graph(analysis: DependencyAnalysis) -> str:
    lines = ["graph TD"]
    for module_name in sorted(analysis.modules):
        node_id = module_name.replace(".", "_").replace("-", "_")
        lines.append(f'    {node_id}["{module_name}"]')
    for edge in sorted(analysis.internal_edges, key=lambda item: (item.source_module, item.target_module)):
        source_id = edge.source_module.replace(".", "_").replace("-", "_")
        target_id = edge.target_module.replace(".", "_").replace("-", "_")
        lines.append(f"    {source_id} --> {target_id}")
    return "\n".join(lines) + "\n"


def render_dependency_report(root: Path, analysis: DependencyAnalysis) -> str:
    fan_out: Counter[str] = Counter()
    fan_in: Counter[str] = Counter()
    for edge in analysis.internal_edges:
        fan_out[edge.source_module] += 1
        fan_in[edge.target_module] += 1

    lines = [
        "# Python Dependency Report",
        "",
        f"- Generated at: `{iso_now()}`",
        f"- Repo root: `{root}`",
        f"- Python modules indexed: `{len(analysis.modules)}`",
        f"- Internal dependency edges: `{len(analysis.internal_edges)}`",
        f"- Standard-library modules referenced: `{len(analysis.stdlib_imports)}`",
        f"- Third-party packages referenced: `{len(analysis.external_imports)}`",
        "",
        "## Mermaid Graph",
        "",
        "See `21_python_dependency_graph.mmd` for the standalone graph artifact.",
        "",
        "## Highest Fan-Out Modules",
        "",
    ]

    top_fan_out = fan_out.most_common(20)
    if top_fan_out:
        for module_name, count in top_fan_out:
            lines.append(f"- `{module_name}` -> `{count}` internal imports")
    else:
        lines.append("- None")

    lines.extend(["", "## Highest Fan-In Modules", ""])
    top_fan_in = fan_in.most_common(20)
    if top_fan_in:
        for module_name, count in top_fan_in:
            lines.append(f"- `{module_name}` <- `{count}` internal dependents")
    else:
        lines.append("- None")

    lines.extend(["", "## Standard Library Modules", ""])
    top_stdlib = analysis.stdlib_imports.most_common(40)
    if top_stdlib:
        for module_name, count in top_stdlib:
            lines.append(f"- `{module_name}` referenced `{count}` time(s)")
    else:
        lines.append("- None")

    lines.extend(["", "## Third-Party Packages", ""])
    top_external = analysis.external_imports.most_common(40)
    if top_external:
        for package_name, count in top_external:
            lines.append(f"- `{package_name}` referenced `{count}` time(s)")
    else:
        lines.append("- None")

    lines.extend(["", "## Module Inventory", ""])
    for module_name in sorted(analysis.modules):
        info = analysis.modules[module_name]
        lines.append(f"- `{module_name}` -> `{info.relative_path}`")

    lines.extend(["", "## Internal Dependency Edges", ""])
    for edge in sorted(analysis.internal_edges, key=lambda item: (item.source_module, item.target_module)):
        lines.append(f"- `{edge.source_module}` -> `{edge.target_module}`")
    if not analysis.internal_edges:
        lines.append("- None")

    return "\n".join(lines).rstrip() + "\n"


def render_architecture_summary(
    root: Path,
    files: list[FileEntry],
    analysis: DependencyAnalysis,
    archived_paths: Iterable[Path],
    output_dir: Path,
    generated_file_names: Iterable[str],
    snapshot_part_count: int,
    snapshot_max_bytes: int,
) -> str:
    top_level_counts = Counter(_top_level_directory(entry.relative_path) for entry in files)
    top_level_bytes = Counter()
    for entry in files:
        top_level_bytes[_top_level_directory(entry.relative_path)] += entry.size_bytes

    largest_files = sorted(files, key=lambda entry: entry.size_bytes, reverse=True)[:20]
    archived_rel = [path.relative_to(root).as_posix() for path in archived_paths]

    lines = [
        "# Architecture Summary",
        "",
        f"- Generated at: `{iso_now()}`",
        f"- Repo root: `{root}`",
        f"- Output directory: `{output_dir.relative_to(root).as_posix()}`",
        f"- Generated artifacts: `{', '.join(generated_file_names)}`",
        f"- Files scanned: `{len(files)}`",
        f"- Python modules indexed: `{len(analysis.modules)}`",
        f"- Internal dependency edges: `{len(analysis.internal_edges)}`",
        f"- Repo snapshot parts: `{snapshot_part_count}`",
        f"- Repo snapshot max part size: `{snapshot_max_bytes}` bytes",
        "",
        "## Top-Level Directory Coverage",
        "",
    ]

    for name, count in top_level_counts.most_common():
        lines.append(f"- `{name}`: `{count}` files, `{top_level_bytes[name]}` bytes")

    lines.extend(["", "## Largest Files", ""])
    for entry in largest_files:
        lines.append(f"- `{entry.relative_path}`: `{entry.size_bytes}` bytes")

    lines.extend(["", "## Archived Prior Artifacts", ""])
    if archived_rel:
        for rel in archived_rel:
            lines.append(f"- `{rel}`")
    else:
        lines.append("- None")

    lines.extend(
        [
            "",
            "## Artifact Guide",
            "",
            "- `10_repo_snapshot.md`: top-level index for the sharded snapshot parts.",
            f"- `{SNAPSHOT_FILE_PREFIX}NNN.md`: concatenated text snapshot of the repo, split into `{snapshot_part_count}` part(s) capped at `{snapshot_max_bytes}` bytes each.",
            "- `20_python_dependency_report.md`: Python module inventory, internal edges, and external import summary.",
            "- `21_python_dependency_graph.mmd`: Mermaid dependency graph for local Python modules.",
        ]
    )

    return "\n".join(lines).rstrip() + "\n"


def write_outputs(output_dir: Path, rendered: dict[str, str]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for file_name, content in rendered.items():
        (output_dir / file_name).write_text(content, encoding="utf-8")


def build_reports(
    root: Path,
    output_dir: Path,
    archive_dir: Path,
    *,
    snapshot_max_bytes: int,
) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    archived_paths = archive_existing_outputs(output_dir, archive_dir)
    files = collect_repo_files(root, output_dir=output_dir)
    analysis = analyze_python_dependencies(root, output_dir=output_dir)
    snapshot_parts = render_repo_snapshot_parts(
        root,
        files,
        max_part_bytes=snapshot_max_bytes,
    )

    rendered = {
        "10_repo_snapshot.md": render_repo_snapshot_index(
            root,
            files,
            snapshot_parts,
            max_part_bytes=snapshot_max_bytes,
        ),
        "20_python_dependency_report.md": render_dependency_report(root, analysis),
        "21_python_dependency_graph.mmd": render_dependency_graph(analysis),
    }
    for snapshot_part in snapshot_parts:
        rendered[snapshot_part.file_name] = snapshot_part.content
    generated_file_names = [
        "00_architecture_summary.md",
        "10_repo_snapshot.md",
        *[part.file_name for part in snapshot_parts],
        *STATIC_OUTPUT_FILE_NAMES[2:],
    ]
    rendered["00_architecture_summary.md"] = render_architecture_summary(
        root,
        files,
        analysis,
        archived_paths,
        output_dir,
        generated_file_names,
        len(snapshot_parts),
        snapshot_max_bytes,
    )
    write_outputs(output_dir, rendered)

    return {
        "archived_paths": archived_paths,
        "files_scanned": len(files),
        "modules_indexed": len(analysis.modules),
        "internal_edges": len(analysis.internal_edges),
        "snapshot_parts": len(snapshot_parts),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Repo root to scan.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for freshly generated architecture artifacts.",
    )
    parser.add_argument(
        "--archive-dir",
        type=Path,
        default=DEFAULT_ARCHIVE_DIR,
        help="Directory for rotated prior architecture artifacts.",
    )
    parser.add_argument(
        "--snapshot-max-bytes",
        type=int,
        default=DEFAULT_SNAPSHOT_MAX_BYTES,
        help="Maximum UTF-8 byte size per repo snapshot part.",
    )
    args = parser.parse_args(argv)

    root = args.root.resolve()
    output_dir = (root / args.output_dir).resolve() if not args.output_dir.is_absolute() else args.output_dir.resolve()
    archive_dir = (root / args.archive_dir).resolve() if not args.archive_dir.is_absolute() else args.archive_dir.resolve()

    result = build_reports(
        root,
        output_dir,
        archive_dir,
        snapshot_max_bytes=args.snapshot_max_bytes,
    )
    print(
        "[architecture-reports] "
        f"scanned {result['files_scanned']} files, "
        f"indexed {result['modules_indexed']} python modules, "
        f"found {result['internal_edges']} internal edges, "
        f"wrote {result['snapshot_parts']} snapshot part(s), "
        f"archived {len(result['archived_paths'])} prior artifact(s)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
