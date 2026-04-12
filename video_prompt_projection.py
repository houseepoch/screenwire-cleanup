#!/usr/bin/env python3
"""Project the exact outbound video request payloads and render a report."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


_SECTION_RE = re.compile(r"(?m)^([A-Z][A-Z /_-]+):\n")


def _frame_sort_key(frame_id: str) -> tuple[int, str]:
    try:
        return int(frame_id.split("_")[1]), frame_id
    except Exception:
        return 10**9, frame_id


def _split_sections(prompt: str) -> list[tuple[str | None, str]]:
    if not prompt.strip():
        return []
    sections: list[tuple[str | None, str]] = []
    matches = list(_SECTION_RE.finditer(prompt))
    if not matches:
        return [(None, prompt.strip())]

    lead = prompt[:matches[0].start()].strip()
    if lead:
        sections.append((None, lead))

    for index, match in enumerate(matches):
        title = match.group(1).strip()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(prompt)
        body = prompt[start:end].strip()
        if body:
            sections.append((title, body))
    return sections


def _strip_audio_dialogue_lines(prompt: str, dialogue_text: str) -> str:
    if not prompt.strip():
        return prompt

    rebuilt: list[str] = []
    for title, body in _split_sections(prompt):
        if title == "AUDIO":
            kept_lines: list[str] = []
            for line in body.splitlines():
                stripped = line.strip()
                if stripped.startswith("- "):
                    bullet = stripped[2:].strip()
                    if "\"" in bullet and ":" in bullet:
                        continue
                    if dialogue_text and dialogue_text.strip() and dialogue_text.strip() in bullet:
                        continue
                kept_lines.append(line.rstrip())
            body = "\n".join(line for line in kept_lines if line.strip()).strip()
            if not body:
                continue

        if title is None:
            rebuilt.append(body.strip())
        else:
            rebuilt.append(f"{title}:\n{body}")
    return "\n\n".join(chunk for chunk in rebuilt if chunk.strip())


def _extract_pose_signal(character: dict[str, Any], prefix: str) -> str:
    pose = character.get("pose") or {}
    for modifier in pose.get("modifiers") or []:
        text = str(modifier).strip()
        if text.startswith(prefix):
            return text.split(":", 1)[1].strip()
    return ""


def build_video_request_projection(prompt_data: dict[str, Any]) -> dict[str, Any]:
    dialogue_text = (prompt_data.get("dialogue_line") or "").strip()
    original_prompt = str(prompt_data.get("prompt") or "")
    motion_prompt = _strip_audio_dialogue_lines(original_prompt, dialogue_text)
    full_prompt = (
        f"{dialogue_text}\n\n{motion_prompt}".strip()
        if dialogue_text
        else motion_prompt.strip()
    )

    direction_signals: list[dict[str, str]] = []
    for character in (prompt_data.get("cast_bible_snapshot") or {}).get("characters") or []:
        direction_signals.append(
            {
                "character_id": str(character.get("character_id") or ""),
                "name": str(character.get("name") or ""),
                "screen_position": _extract_pose_signal(character, "screen_position:"),
                "facing_direction": _extract_pose_signal(character, "facing_direction:"),
                "looking_at": _extract_pose_signal(character, "looking_at:"),
                "eye_direction": _extract_pose_signal(character, "eye_direction:"),
                "action": _extract_pose_signal(character, "action:"),
                "emotion": _extract_pose_signal(character, "emotion:"),
            }
        )

    directing = prompt_data.get("directing") or {}
    issues: list[str] = []
    if prompt_data.get("dialogue_present") and not dialogue_text:
        issues.append("dialogue_present=true but dialogue_text is empty")
    if not str(prompt_data.get("shot_type") or "").strip():
        issues.append("shot_type missing")
    if not str(prompt_data.get("camera_motion") or "").strip():
        issues.append("camera_motion missing")
    if direction_signals and not any(item["facing_direction"] for item in direction_signals):
        issues.append("cast direction missing: no facing_direction in cast_bible_snapshot")
    if direction_signals and not any(item["looking_at"] or item["eye_direction"] for item in direction_signals):
        issues.append("eyeline missing: no looking_at or eye_direction in cast_bible_snapshot")
    if prompt_data.get("dialogue_present") and not str(directing.get("camera_motivation") or "").strip():
        issues.append("dialogue beat missing camera_motivation")

    return {
        "frame_id": prompt_data.get("frame_id"),
        "scene_id": prompt_data.get("scene_id"),
        "input_image_path": prompt_data.get("input_image_path"),
        "duration": prompt_data.get("duration"),
        "dialogue_present": bool(prompt_data.get("dialogue_present")),
        "dialogue_turn_count": int(prompt_data.get("dialogue_turn_count") or 0),
        "dialogue_fit_status": prompt_data.get("dialogue_fit_status"),
        "dialogue_text": dialogue_text,
        "motion_prompt": motion_prompt,
        "full_prompt": full_prompt,
        "shot_type": prompt_data.get("shot_type"),
        "camera_motion": prompt_data.get("camera_motion"),
        "voice_delivery": prompt_data.get("voice_delivery"),
        "direction_signals": direction_signals,
        "directing": directing,
        "issues": issues,
    }


def _render_projection_markdown(project_dir: Path, payloads: list[dict[str, Any]]) -> str:
    issue_counts: Counter[str] = Counter()
    fit_counts: Counter[str] = Counter()
    dialogue_frames = 0
    with_facing = 0
    with_eyeline = 0
    with_camera_motivation = 0

    for payload in payloads:
        fit_counts[str(payload.get("dialogue_fit_status") or "unknown")] += 1
        if payload.get("dialogue_present"):
            dialogue_frames += 1
        if any(item.get("facing_direction") for item in payload.get("direction_signals", [])):
            with_facing += 1
        if any(item.get("looking_at") or item.get("eye_direction") for item in payload.get("direction_signals", [])):
            with_eyeline += 1
        if str((payload.get("directing") or {}).get("camera_motivation") or "").strip():
            with_camera_motivation += 1
        for issue in payload.get("issues", []):
            issue_counts[issue] += 1

    lines: list[str] = []
    lines.append("# Video Prompt Projection")
    lines.append("")
    lines.append(f"- Project: `{project_dir.name}`")
    lines.append("- Scope: exact outbound payloads as Phase 5 would send them to `/internal/generate-video`")
    lines.append(f"- Projected frame count: `{len(payloads)}`")
    lines.append(f"- Dialogue-bearing frames: `{dialogue_frames}`")
    lines.append(f"- Frames with facing signals in cast snapshot: `{with_facing}`")
    lines.append(f"- Frames with eyeline signals in cast snapshot: `{with_eyeline}`")
    lines.append(f"- Frames with camera motivation in directing: `{with_camera_motivation}`")
    lines.append("")

    lines.append("## Dialogue Fit Status")
    lines.append("")
    for key, count in fit_counts.most_common():
        lines.append(f"- `{key}`: `{count}`")
    if not fit_counts:
        lines.append("- No projected video payloads found")
    lines.append("")

    lines.append("## Projection Issues")
    lines.append("")
    for issue, count in issue_counts.most_common():
        lines.append(f"- `{issue}`: `{count}`")
    if not issue_counts:
        lines.append("- No projection issues detected")
    lines.append("")

    lines.append("## Per-Frame Payloads")
    lines.append("")
    for payload in payloads:
        frame_id = payload.get("frame_id") or "unknown"
        lines.append(f"### `{frame_id}`")
        lines.append("")
        lines.append(f"- Scene: `{payload.get('scene_id')}`")
        lines.append(f"- Image path: `{payload.get('input_image_path')}`")
        lines.append(f"- Duration: `{payload.get('duration')}`")
        lines.append(f"- Shot type: `{payload.get('shot_type')}`")
        lines.append(f"- Camera motion: `{payload.get('camera_motion')}`")
        lines.append(f"- Dialogue present: `{payload.get('dialogue_present')}`")
        lines.append(f"- Dialogue turns: `{payload.get('dialogue_turn_count')}`")
        lines.append(f"- Dialogue fit: `{payload.get('dialogue_fit_status')}`")
        if payload.get("issues"):
            lines.append("- Issues:")
            for issue in payload["issues"]:
                lines.append(f"  - {issue}")
        lines.append("")
        lines.append("#### Dialogue Prefix")
        lines.append("")
        lines.append("```text")
        lines.append(payload.get("dialogue_text") or "")
        lines.append("```")
        lines.append("")
        lines.append("#### Motion Prompt")
        lines.append("")
        lines.append("```text")
        lines.append(payload.get("motion_prompt") or "")
        lines.append("```")
        lines.append("")
        lines.append("#### Direction Signals")
        lines.append("")
        for signal in payload.get("direction_signals", []):
            lines.append(
                "- "
                + ", ".join(
                    f"{key}={value!r}"
                    for key, value in signal.items()
                    if value
                )
            )
        if not payload.get("direction_signals"):
            lines.append("- No cast direction signals")
        lines.append("")
    return "\n".join(lines)


def generate_video_prompt_projection(project_dir: Path) -> tuple[Path, Path]:
    project_dir = project_dir.resolve()
    reports_dir = project_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    prompt_dir = project_dir / "video" / "prompts"
    payloads: list[dict[str, Any]] = []
    for path in sorted(prompt_dir.glob("*_video.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        payloads.append(build_video_request_projection(data))
    payloads.sort(key=lambda item: _frame_sort_key(str(item.get("frame_id") or "")))

    json_path = reports_dir / "video_prompt_projection.json"
    md_path = reports_dir / "video_prompt_projection.md"
    json_path.write_text(json.dumps(payloads, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(_render_projection_markdown(project_dir, payloads), encoding="utf-8")
    return md_path, json_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate video prompt projection reports.")
    parser.add_argument("--project-dir", required=True, help="Project directory")
    args = parser.parse_args()
    md_path, json_path = generate_video_prompt_projection(Path(args.project_dir))
    print(md_path)
    print(json_path)


if __name__ == "__main__":
    main()
