"""Deterministic dialogue workflow validation for ScreenWire."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

if __package__:
    from .api import get_frame_context
    from .store import GraphStore
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from graph.api import get_frame_context
    from graph.store import GraphStore

from screenwire_contracts import default_dialogue_workflow


@dataclass
class DialogueIssue:
    frame_id: str
    severity: str
    problem: str
    suggested_fix: str


def _read_onboarding_config(project_dir: Path) -> dict[str, Any]:
    path = project_dir / "source_files" / "onboarding_config.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _ratio(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def _balanced_ok(raw_line: str, assigned_line: str) -> bool:
    raw = _normalize_text(raw_line)
    assigned = _normalize_text(assigned_line)
    if raw == assigned:
        return True
    if not raw or not assigned:
        return False
    raw_tokens = raw.split()
    assigned_tokens = assigned.split()
    token_delta = abs(len(raw_tokens) - len(assigned_tokens))
    return _ratio(raw, assigned) >= 0.92 and token_delta <= 2


def _creative_ok(raw_line: str, assigned_line: str) -> bool:
    raw = _normalize_text(raw_line)
    assigned = _normalize_text(assigned_line)
    if raw == assigned:
        return True
    if not raw or not assigned:
        return False
    return _ratio(raw, assigned) >= 0.72


def _tier_compliance(tier: str, raw_line: str, assigned_line: str) -> tuple[str, str | None]:
    raw = _normalize_text(raw_line)
    assigned = _normalize_text(assigned_line)
    if tier == "strict":
        if raw != assigned:
            return "fail", "Strict tier requires word-for-word dialogue."
        return "pass", None
    if tier == "balanced":
        if not _balanced_ok(raw, assigned):
            return "fail", "Balanced tier only permits very light delivery smoothing."
        return "pass", None
    if tier == "creative":
        if not _creative_ok(raw, assigned):
            return "fail", "Creative tier allows reframing, but the line drifted too far from the source."
        return "pass", None
    return "pass", None


def validate_dialogue_project(project_dir: Path) -> dict[str, Any]:
    store = GraphStore(project_dir)
    graph = store.load()
    config = _read_onboarding_config(project_dir)
    creative_freedom = (
        config.get("creativeFreedom")
        or getattr(graph.project, "creative_freedom", "")
        or "balanced"
    ).strip().lower()
    workflow = config.get("dialogueWorkflow") or default_dialogue_workflow()
    enabled = bool(workflow.get("enabled", True))

    video_prompt_dir = project_dir / "video" / "prompts"
    video_prompts: dict[str, dict[str, Any]] = {}
    if video_prompt_dir.exists():
        for path in sorted(video_prompt_dir.glob("*_video.json")):
            try:
                video_prompts[path.stem.removesuffix("_video")] = json.loads(
                    path.read_text(encoding="utf-8")
                )
            except (json.JSONDecodeError, OSError):
                continue

    issues: list[DialogueIssue] = []
    assignments: list[dict[str, Any]] = []
    dialogue_frame_count = 0

    if enabled:
        for frame_id in graph.frame_order:
            ctx = get_frame_context(graph, frame_id)
            audible = [node for node in ctx.get("dialogue", []) if _normalize_text(node.get("raw_line") or node.get("line"))]
            prompt_state = video_prompts.get(frame_id, {})

            if audible:
                dialogue_frame_count += 1
                if not prompt_state:
                    issues.append(
                        DialogueIssue(
                            frame_id=frame_id,
                            severity="ERROR",
                            problem="Frame has dialogue but no assembled video prompt JSON.",
                            suggested_fix="Re-run graph_assemble_prompts after dialogue recovery.",
                        )
                    )
                    continue

                if not prompt_state.get("dialogue_present", False):
                    issues.append(
                        DialogueIssue(
                            frame_id=frame_id,
                            severity="ERROR",
                            problem="Frame has dialogue but video prompt marks dialogue_present=false.",
                            suggested_fix="Rebuild the shot packet and video prompt so dialogue metadata is carried through.",
                        )
                    )

                prompt_line = _normalize_text(prompt_state.get("dialogue_line", ""))
                if not prompt_line:
                    issues.append(
                        DialogueIssue(
                            frame_id=frame_id,
                            severity="ERROR",
                            problem="Frame has dialogue but video prompt has no dialogue_line.",
                            suggested_fix="Populate dialogue_line from the audible dialogue chunk for this frame.",
                        )
                    )

                allowed_lines = [
                    _normalize_text(node.get("line") or node.get("raw_line") or "")
                    for node in audible
                ]
                if prompt_line and not any(
                    prompt_line == candidate
                    or prompt_line in candidate
                    or candidate in prompt_line
                    for candidate in allowed_lines
                    if candidate
                ):
                    issues.append(
                        DialogueIssue(
                            frame_id=frame_id,
                            severity="ERROR" if creative_freedom in {"strict", "balanced"} else "WARNING",
                            problem="Video prompt dialogue_line does not match the graph-assigned audible dialogue.",
                            suggested_fix="Reassemble the video prompt from current dialogue nodes before proceeding.",
                        )
                    )

                for node in audible:
                    raw_line = node.get("raw_line") or node.get("line") or ""
                    assigned_line = node.get("line") or raw_line
                    compliance, reason = _tier_compliance(creative_freedom, raw_line, assigned_line)
                    assignments.append(
                        {
                            "frame_id": frame_id,
                            "dialogue_id": node.get("dialogue_id", ""),
                            "speaker": node.get("speaker", ""),
                            "raw_line": raw_line,
                            "assigned_line": assigned_line,
                            "tier_compliance": compliance,
                        }
                    )
                    if reason:
                        issues.append(
                            DialogueIssue(
                                frame_id=frame_id,
                                severity="ERROR",
                                problem=reason,
                                suggested_fix="Reset the assigned line to the source-aligned version or lower the creative freedom tier.",
                            )
                        )
            elif prompt_state.get("dialogue_present", False):
                issues.append(
                    DialogueIssue(
                        frame_id=frame_id,
                        severity="WARNING",
                        problem="Video prompt claims dialogue is present but the graph has no audible dialogue for this frame.",
                        suggested_fix="Rebuild the shot packet and prompt after checking frame dialogue spans.",
                    )
                )

    error_count = sum(1 for issue in issues if issue.severity == "ERROR")
    report = {
        "status": "pass" if error_count == 0 else "fail",
        "creativeFreedom": creative_freedom,
        "dialogueWorkflowVersion": workflow.get("version", "unknown"),
        "summary": {
            "dialogueNodeCount": len(graph.dialogue_order or graph.dialogue),
            "dialogueFrameCount": dialogue_frame_count,
            "videoPromptCount": len(video_prompts),
            "issues": len(issues),
            "errors": error_count,
            "warnings": len(issues) - error_count,
        },
        "issues": [asdict(issue) for issue in issues],
        "assignments": assignments,
    }

    report_path = project_dir / "logs" / "pipeline" / "dialogue_confirmation_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate dialogue mapping and tier compliance.")
    parser.add_argument("--project-dir", default=".")
    args = parser.parse_args()

    report = validate_dialogue_project(Path(args.project_dir))
    print(json.dumps(report["summary"], indent=2))
    raise SystemExit(0 if report["status"] == "pass" else 1)


if __name__ == "__main__":
    main()
