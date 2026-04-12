"""Local CLI-compatible Grok runner used as the repo's process-based LLM adapter."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from llm.xai_client import (
    DEFAULT_REASONING_MODEL,
    DEFAULT_STAGE1_REASONING_MODEL,
    SyncXAIClient,
    build_prompt_cache_key,
    is_multi_agent_model,
    resolve_model_alias,
)
from llm.project_tools import build_project_tools, make_project_tool_executor


def _resolve_cache_key(default_key: str) -> str:
    return os.environ.get("XAI_PROMPT_CACHE_KEY", "").strip() or default_key


def _emit(content: str, output_format: str) -> None:
    if output_format == "stream-json":
        payload = {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": content,
            },
        }
        sys.stdout.write(json.dumps(payload) + "\n")
    else:
        sys.stdout.write(content)
        if not content.endswith("\n"):
            sys.stdout.write("\n")
    sys.stdout.flush()


def _read_system_prompt(args: argparse.Namespace) -> str:
    if args.system_prompt_file:
        return args.system_prompt_file.read()
    return args.system_prompt or ""


def _should_enable_project_tools(task_hint: str, system_prompt: str) -> bool:
    lowered_hint = (task_hint or "").lower()
    if lowered_hint.startswith("frame_enricher_worker_"):
        return False
    if any(token in lowered_hint for token in ("creative_coordinator", "prose_worker", "director")):
        return True
    lowered_prompt = system_prompt.lower()
    return "your working directory is the project root" in lowered_prompt or "available skills" in lowered_prompt


def _tool_reasoning_model(task_hint: str) -> str:
    lowered_hint = (task_hint or "").lower()
    if any(token in lowered_hint for token in ("creative_coordinator", "prose_worker", "director")):
        return DEFAULT_STAGE1_REASONING_MODEL
    return DEFAULT_REASONING_MODEL


def _run_print_mode(args: argparse.Namespace) -> int:
    system_prompt = _read_system_prompt(args)
    task_hint = args.task_hint or ""
    model = resolve_model_alias(args.model, task_hint=task_hint)
    cache_key = _resolve_cache_key(
        build_prompt_cache_key("agent-runner-print", model, system_prompt)
    )
    client = SyncXAIClient()
    project_dir_raw = os.environ.get("PROJECT_DIR", "")
    repo_root = Path(__file__).resolve().parent.parent
    use_project_tools = bool(project_dir_raw) and _should_enable_project_tools(task_hint, system_prompt)

    if use_project_tools:
        project_root = Path(project_dir_raw).resolve()
        skills_dir = Path(os.environ.get("SKILLS_DIR", str(repo_root / "skills"))).resolve()
        tool_executor = make_project_tool_executor(
            project_root=project_root,
            repo_root=repo_root,
            skills_dir=skills_dir,
        )
        tool_model = model if not is_multi_agent_model(model) else _tool_reasoning_model(task_hint)
        tool_task_hint = task_hint if tool_model == model else ""
        content = client.generate_text_with_tools(
            prompt=args.prompt or "",
            system_prompt=system_prompt,
            tools=build_project_tools(),
            tool_executor=tool_executor,
            model=tool_model,
            task_hint=tool_task_hint,
            cache_key=cache_key,
        )
    elif is_multi_agent_model(model):
        content, _ = client.generate_multi_agent(
            prompt=args.prompt or "",
            system_prompt=system_prompt,
            model=model,
            cache_key=cache_key,
        )
    else:
        content = client.generate_text(
            prompt=args.prompt or "",
            system_prompt=system_prompt,
            model=model,
            task_hint=task_hint,
            cache_key=cache_key,
        )

    _emit(content, args.output_format)
    return 0


def _run_interactive_mode(args: argparse.Namespace) -> int:
    system_prompt = _read_system_prompt(args)
    task_hint = args.task_hint or ""
    model = resolve_model_alias(args.model, task_hint=task_hint)
    cache_key = _resolve_cache_key(
        build_prompt_cache_key("agent-runner-interactive", model, system_prompt)
    )
    client = SyncXAIClient()

    messages: list[dict[str, Any]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    previous_response_id: str | None = None

    for raw_line in sys.stdin:
        prompt = raw_line.strip()
        if not prompt:
            continue

        if is_multi_agent_model(model):
            if previous_response_id:
                content, previous_response_id = client.generate_multi_agent(
                    input_messages=[{"role": "user", "content": prompt}],
                    model=model,
                    cache_key=cache_key,
                    previous_response_id=previous_response_id,
                )
            else:
                content, previous_response_id = client.generate_multi_agent(
                    prompt=prompt,
                    system_prompt=system_prompt,
                    model=model,
                    cache_key=cache_key,
                )
        else:
            messages.append({"role": "user", "content": prompt})
            content = client.generate_text(
                messages=messages,
                model=model,
                task_hint=task_hint,
                cache_key=cache_key,
            )
            messages.append({"role": "assistant", "content": content})

        _emit(content, args.output_format)

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="ScreenWire local Grok agent runner")
    parser.add_argument("-p", "--prompt", default="")
    parser.add_argument("--print", action="store_true", dest="print_mode")
    parser.add_argument("--system-prompt", default="")
    parser.add_argument("--system-prompt-file", type=argparse.FileType("r", encoding="utf-8"))
    parser.add_argument("--dangerously-skip-permissions", action="store_true")
    parser.add_argument("--output-format", choices=("text", "stream-json"), default="text")
    parser.add_argument("--model", default=DEFAULT_REASONING_MODEL)
    parser.add_argument("--task-hint", default="")
    args = parser.parse_args()

    try:
        if args.print_mode:
            return _run_print_mode(args)
        return _run_interactive_mode(args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        sys.stderr.write(f"{type(exc).__name__}: {exc}\n")
        sys.stderr.flush()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
