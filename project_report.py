#!/usr/bin/env python3
"""Generate a concatenated non-media project report after prompt assembly."""

from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from video_prompt_projection import generate_video_prompt_projection

TEXT_SUFFIXES = {
    ".json",
    ".jsonl",
    ".log",
    ".md",
    ".mmd",
    ".py",
    ".txt",
    ".yaml",
    ".yml",
}

INCLUDE_PATHS = [
    "project_manifest.json",
    "dialogue.json",
    "source_files",
    "creative_output",
    "graph",
    "cast",
    "locations",
    "props",
    "frames/prompts",
    "frames/shot_packets",
    "frames/storyboard_prompts",
    "video/prompts",
    "logs/pipeline",
    "scripts",
]

EXCLUDE_PARTS = {
    "archive",
    "assembled",
    "audio",
    "clips",
    "composed",
    "export",
    "generated",
    "primary",
    "storyboards",
}

GRAPH_REGISTRIES = [
    "cast",
    "locations",
    "props",
    "scenes",
    "frames",
    "dialogue",
    "storyboard_grids",
    "cast_frame_states",
    "prop_frame_states",
    "location_frame_states",
    "edges",
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _slug(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M%SZ")


def _archive_previous_report(reports_dir: Path) -> Path | None:
    report_path = reports_dir / "project_report.md"
    snapshot_dir = reports_dir / "snapshot"
    if not report_path.exists() and not snapshot_dir.exists():
        return None
    archive_dir = reports_dir / "archive" / _slug(_now())
    archive_dir.mkdir(parents=True, exist_ok=True)
    if report_path.exists():
        shutil.move(str(report_path), str(archive_dir / "project_report.md"))
    if snapshot_dir.exists():
        shutil.move(str(snapshot_dir), str(archive_dir / "snapshot"))
    return archive_dir


def _is_text_file(path: Path) -> bool:
    return path.suffix.lower() in TEXT_SUFFIXES


def _include_file(project_dir: Path, path: Path) -> bool:
    rel = path.relative_to(project_dir)
    if rel.name == "project_report.md":
        return False
    if any(part in EXCLUDE_PARTS for part in rel.parts):
        return False
    return path.is_file() and _is_text_file(path)


def _iter_snapshot_files(project_dir: Path) -> list[Path]:
    files: list[Path] = []
    seen: set[Path] = set()
    for raw_root in INCLUDE_PATHS:
        root = project_dir / raw_root
        if not root.exists():
            continue
        candidates = [root] if root.is_file() else sorted(root.rglob("*"))
        for path in candidates:
            if path in seen:
                continue
            if _include_file(project_dir, path):
                files.append(path)
                seen.add(path)
    return sorted(files, key=lambda p: p.relative_to(project_dir).as_posix())


def _render_tree(files: list[Path], project_dir: Path) -> list[str]:
    tree: dict[str, Any] = {}
    for path in files:
        rel = path.relative_to(project_dir)
        cursor = tree
        for part in rel.parts[:-1]:
            cursor = cursor.setdefault(part, {})
        cursor[rel.parts[-1]] = None

    lines: list[str] = []

    def _walk(node: dict[str, Any], prefix: str = "") -> None:
        keys = sorted(node)
        for index, key in enumerate(keys):
            connector = "└── " if index == len(keys) - 1 else "├── "
            lines.append(f"{prefix}{connector}{key}")
            child = node[key]
            if isinstance(child, dict):
                extension = "    " if index == len(keys) - 1 else "│   "
                _walk(child, prefix + extension)

    _walk(tree)
    return lines


def _load_graph(project_dir: Path) -> dict[str, Any] | None:
    graph_path = project_dir / "graph" / "narrative_graph.json"
    if not graph_path.exists():
        return None
    return json.loads(graph_path.read_text(encoding="utf-8"))


def _collect_path_counts(value: Any, prefix: tuple[str, ...], counter: Counter[tuple[str, ...]]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            path = prefix + (key,)
            counter[path] += 1
            _collect_path_counts(child, path, counter)
        return
    if isinstance(value, list):
        counter[prefix + ("[]",)] += len(value)
        for child in value:
            _collect_path_counts(child, prefix + ("[]",), counter)


def _field_usage_by_registry(graph: dict[str, Any]) -> dict[str, Counter[tuple[str, ...]]]:
    usage: dict[str, Counter[tuple[str, ...]]] = {}
    for registry in GRAPH_REGISTRIES:
        value = graph.get(registry)
        counter: Counter[tuple[str, ...]] = Counter()
        if isinstance(value, dict):
            for item in value.values():
                _collect_path_counts(item, (), counter)
        elif isinstance(value, list):
            for item in value:
                _collect_path_counts(item, (), counter)
        usage[registry] = counter
    return usage


def _render_field_usage(counter: Counter[tuple[str, ...]]) -> list[str]:
    tree: dict[str, Any] = {}
    for path, count in sorted(counter.items()):
        cursor = tree
        for part in path:
            cursor = cursor.setdefault(part, {})
        cursor["__count__"] = count

    lines: list[str] = []

    def _walk(node: dict[str, Any], depth: int = 0) -> None:
        for key in sorted(k for k in node if k != "__count__"):
            value = node[key]
            indent = "  " * depth
            count = value.get("__count__") if isinstance(value, dict) else None
            if isinstance(value, dict):
                suffix = f": `{count}`" if count is not None else ""
                lines.append(f"{indent}- `{key}`{suffix}")
                _walk(value, depth + 1)

    _walk(tree)
    return lines


def _edge_stats(graph: dict[str, Any]) -> tuple[Counter[str], list[tuple[str, int, int]]]:
    edge_type_counts: Counter[str] = Counter()
    indegree: Counter[str] = Counter()
    outdegree: Counter[str] = Counter()
    for edge in graph.get("edges", []):
        edge_type_counts[edge.get("edge_type", "unknown")] += 1
        source_id = edge.get("source_id")
        target_id = edge.get("target_id")
        if source_id:
            outdegree[source_id] += 1
        if target_id:
            indegree[target_id] += 1
    node_ids = set(indegree) | set(outdegree)
    top_nodes = sorted(
        ((node_id, indegree[node_id], outdegree[node_id]) for node_id in node_ids),
        key=lambda item: (item[1] + item[2], item[2], item[1], item[0]),
        reverse=True,
    )[:25]
    return edge_type_counts, top_nodes


def _prompt_reference_usage(prompt_files: list[Path]) -> Counter[str]:
    refs: Counter[str] = Counter()
    for path in prompt_files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for ref in payload.get("reference_images") or []:
            ref_text = str(ref)
            ref_path = Path(ref_text)
            if any(part in EXCLUDE_PARTS for part in ref_path.parts):
                continue
            refs[ref_text] += 1
    return refs


def _fence_for(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".json":
        return "json"
    if suffix == ".md":
        return "md"
    if suffix == ".py":
        return "python"
    if suffix in {".yaml", ".yml"}:
        return "yaml"
    return "text"


def _frame_sort_key(frame_id: str) -> tuple[int, str]:
    try:
        return int(frame_id.split("_")[1]), frame_id
    except Exception:
        return 10**9, frame_id


def _collapse_frame_ranges(frame_ids: list[str]) -> list[str]:
    if not frame_ids:
        return []
    sorted_ids = sorted(frame_ids, key=_frame_sort_key)
    numbers: list[int] = []
    for frame_id in sorted_ids:
        try:
            numbers.append(int(frame_id.split("_")[1]))
        except Exception:
            continue
    if not numbers:
        return sorted_ids
    ranges: list[str] = []
    start = prev = numbers[0]
    for number in numbers[1:]:
        if number == prev + 1:
            prev = number
            continue
        ranges.append(f"f_{start:03d}" if start == prev else f"f_{start:03d}-f_{prev:03d}")
        start = prev = number
    ranges.append(f"f_{start:03d}" if start == prev else f"f_{start:03d}-f_{prev:03d}")
    return ranges


def _load_quality_gate(project_dir: Path, phase_num: int) -> dict[str, Any] | None:
    path = project_dir / "logs" / "pipeline" / f"phase_{phase_num}_quality_gate.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _output_coverage(
    project_dir: Path,
    graph: dict[str, Any] | None,
    image_prompts: list[Path],
) -> dict[str, Any]:
    prompt_ids = {path.stem.replace("_image", "") for path in image_prompts}
    composed_ids = {
        path.stem.replace("_gen", "")
        for path in (project_dir / "frames" / "composed").glob("f_*_gen.png")
    }
    graph_frame_ids = set(graph.get("frames", {}).keys()) if graph is not None else set()
    planned_ids = graph_frame_ids or prompt_ids
    missing_ids = sorted(prompt_ids - composed_ids, key=_frame_sort_key)

    by_location: dict[str, dict[str, int]] = {}
    if graph is not None:
        for frame_id, frame in graph.get("frames", {}).items():
            location_id = frame.get("location_id") or "unknown"
            bucket = by_location.setdefault(location_id, {"total": 0, "generated": 0})
            bucket["total"] += 1
            if frame_id in composed_ids:
                bucket["generated"] += 1

    return {
        "planned_frame_count": len(planned_ids),
        "image_prompt_count": len(prompt_ids),
        "composed_frame_count": len(composed_ids),
        "missing_ids": missing_ids,
        "missing_ranges": _collapse_frame_ranges(missing_ids),
        "phase4_quality_gate": _load_quality_gate(project_dir, 4),
        "by_location": by_location,
    }


def generate_project_report(project_dir: Path) -> Path:
    project_dir = project_dir.resolve()
    reports_dir = project_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "archive").mkdir(parents=True, exist_ok=True)
    archived = _archive_previous_report(reports_dir)

    snapshot_files = _iter_snapshot_files(project_dir)
    graph = _load_graph(project_dir)
    manifest = json.loads((project_dir / "project_manifest.json").read_text(encoding="utf-8"))
    tree_lines = _render_tree(snapshot_files, project_dir)

    image_prompts = sorted((project_dir / "frames" / "prompts").glob("*_image.json")) if (project_dir / "frames" / "prompts").exists() else []
    video_prompts = sorted((project_dir / "video" / "prompts").glob("*_video.json")) if (project_dir / "video" / "prompts").exists() else []
    output_coverage = _output_coverage(project_dir, graph, image_prompts)
    video_projection_md, video_projection_json = generate_video_prompt_projection(project_dir)

    lines: list[str] = []
    lines.append("# Project Report")
    lines.append("")
    lines.append(f"- Project: `{project_dir.name}`")
    lines.append(f"- Generated at: `{_now().isoformat()}`")
    lines.append(f"- Report path: `reports/project_report.md`")
    if archived is not None:
        lines.append(f"- Previous report archived to: `{archived.relative_to(project_dir).as_posix()}`")
    lines.append("")

    lines.append("## Snapshot Coverage")
    lines.append("")

    lines.append("## Video Prompt Projection")
    lines.append("")
    lines.append(
        f"- Video request projection report: `{video_projection_md.relative_to(project_dir).as_posix()}`"
    )
    lines.append(
        f"- Video request projection JSON: `{video_projection_json.relative_to(project_dir).as_posix()}`"
    )
    lines.append("")
    lines.append(f"- Text snapshot files included: `{len(snapshot_files)}`")
    lines.append(f"- Image prompt files: `{len(image_prompts)}`")
    lines.append(f"- Video prompt files: `{len(video_prompts)}`")
    if graph is not None:
        for registry in GRAPH_REGISTRIES:
            value = graph.get(registry)
            if isinstance(value, dict):
                lines.append(f"- Graph `{registry}` count: `{len(value)}`")
            elif isinstance(value, list):
                lines.append(f"- Graph `{registry}` count: `{len(value)}`")
    lines.append("")

    lines.append("## Snapshot Tree")
    lines.append("")
    lines.append("```text")
    lines.extend(tree_lines or ["(no text snapshot files found)"])
    lines.append("```")
    lines.append("")

    lines.append("## Output Coverage")
    lines.append("")
    lines.append(f"- Planned graph frames: `{output_coverage['planned_frame_count']}`")
    lines.append(f"- Image prompts assembled: `{output_coverage['image_prompt_count']}`")
    lines.append(f"- Composed frames generated: `{output_coverage['composed_frame_count']}`")
    lines.append(f"- Missing composed frames: `{len(output_coverage['missing_ids'])}`")
    phase4_gate = output_coverage["phase4_quality_gate"]
    if phase4_gate is not None:
        lines.append(f"- Phase 4 quality gate passed: `{phase4_gate.get('passed')}`")
        lines.append(f"- Phase 4 quality gate issue count: `{len(phase4_gate.get('issues', []))}`")
    lines.append("")

    lines.append("### Missing Composed Frame IDs")
    lines.append("")
    missing_ranges = output_coverage["missing_ranges"]
    if missing_ranges:
        for group in missing_ranges:
            lines.append(f"- `{group}`")
    else:
        lines.append("- No missing composed frames")
    lines.append("")

    lines.append("### Per-Location Composed Coverage")
    lines.append("")
    if output_coverage["by_location"]:
        for location_id, stats in sorted(output_coverage["by_location"].items()):
            total = stats["total"]
            generated = stats["generated"]
            rate = 0.0 if total == 0 else (generated / total) * 100
            lines.append(f"- `{location_id}`: `{generated}/{total}` (`{rate:.1f}%`)")
    else:
        lines.append("- No graph-backed location coverage available")
    lines.append("")

    if phase4_gate is not None and phase4_gate.get("issues"):
        lines.append("### Phase 4 Quality Gate Issues")
        lines.append("")
        for issue in phase4_gate.get("issues", []):
            lines.append(f"- {issue}")
        lines.append("")

    if graph is not None:
        edge_type_counts, top_nodes = _edge_stats(graph)
        field_usage = _field_usage_by_registry(graph)

        lines.append("## Graph Edge Usage")
        lines.append("")
        for edge_type, count in edge_type_counts.most_common():
            lines.append(f"- `{edge_type}`: `{count}`")
        if not edge_type_counts:
            lines.append("- No edges found")
        lines.append("")

        lines.append("## Graph Node Degree")
        lines.append("")
        for node_id, indeg, outdeg in top_nodes:
            lines.append(f"- `{node_id}`: in=`{indeg}` out=`{outdeg}` total=`{indeg + outdeg}`")
        if not top_nodes:
            lines.append("- No node degree data found")
        lines.append("")

        lines.append("## Graph Field Usage")
        lines.append("")
        for registry in GRAPH_REGISTRIES:
            lines.append(f"### `{registry}`")
            usage_lines = _render_field_usage(field_usage.get(registry, Counter()))
            lines.extend(usage_lines or ["- No fields found"])
            lines.append("")

    prompt_refs = _prompt_reference_usage(image_prompts)
    lines.append("## Prompt Reference Usage")
    lines.append("")
    for ref_path, count in prompt_refs.most_common():
        lines.append(f"- `{ref_path}`: `{count}`")
    if not prompt_refs:
        lines.append("- No prompt reference images found")
    lines.append("")

    lines.append("## Manifest Snapshot")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(manifest, indent=2, ensure_ascii=False))
    lines.append("```")
    lines.append("")

    lines.append("## Full Text Snapshot")
    lines.append("")
    for path in snapshot_files:
        rel = path.relative_to(project_dir).as_posix()
        lines.append(f"### `{rel}`")
        lines.append("")
        lines.append(f"```{_fence_for(path)}")
        lines.append(path.read_text(encoding="utf-8"))
        lines.append("```")
        lines.append("")

    report_path = reports_dir / "project_report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a concatenated project report")
    parser.add_argument("--project-dir", default=".", help="Project directory")
    args = parser.parse_args()
    report_path = generate_project_report(Path(args.project_dir))
    print(f"SUCCESS: Project report written to {report_path}")


if __name__ == "__main__":
    main()
