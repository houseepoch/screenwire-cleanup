#!/usr/bin/env python3
"""ScreenWire AI — Agent Training Mode

Spawns individual pipeline agents as interactive Grok-backed sessions so you
can talk to them directly, see their full terminal output, and iterate on
their behavior in real time.

Usage:
    python3 train_agent.py                      # interactive menu
    python3 train_agent.py creative_coordinator # jump straight to an agent
    python3 train_agent.py --list               # list agents and exit
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from llm.xai_client import DEFAULT_REASONING_MODEL

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

APP_DIR = Path(__file__).resolve().parent
PROMPTS_DIR = APP_DIR / "agent_prompts"
SKILLS_DIR = APP_DIR / "skills"
PROJECTS_DIR = APP_DIR / "projects"

DEFAULT_MODEL = DEFAULT_REASONING_MODEL

_INCLUDE_RE = re.compile(r'\{\{include:(.+?)\}\}')


def _expand_includes(text: str, base_dir: Path) -> str:
    """Replace {{include:path}} markers with file contents."""
    def _replacer(match: re.Match) -> str:
        inc_path = base_dir / match.group(1)
        if inc_path.exists():
            return inc_path.read_text()
        print(f"{YELLOW}WARN: Include file not found: {inc_path}{RESET}")
        return match.group(0)
    return _INCLUDE_RE.sub(_replacer, text)


def _deploy_shared_conventions(project_dir: Path) -> None:
    """Copy shared_conventions.md → project_dir/CLAUDE.md if stale or missing."""
    source = APP_DIR / "shared_conventions.md"
    target = project_dir / "CLAUDE.md"
    if not source.exists():
        return
    if target.exists() and target.stat().st_mtime >= source.stat().st_mtime:
        return
    shutil.copy2(source, target)

# ---------------------------------------------------------------------------
# Agent registry — order follows the pipeline phases
# ---------------------------------------------------------------------------

AGENTS = [
    ("director",                "Director — orchestrates project lifecycle & reviews gates"),
    ("creative_coordinator",    "Creative Coordinator — narrative contracts, skeleton, assembly"),
    # Archived Morpheus 1-4 helpers previously covered entity seeding, frame parsing,
    # dialogue wiring, and compositing before the deterministic graph pipeline.
    # image_verifier, composition_verifier, video_verifier — REMOVED (phases 3-5 fully programmatic)
]

AGENT_IDS = [a[0] for a in AGENTS]

# ---------------------------------------------------------------------------
# Terminal formatting
# ---------------------------------------------------------------------------

RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
CYAN   = "\033[36m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
RED    = "\033[31m"
WHITE  = "\033[97m"
BG_DARK = "\033[48;5;235m"


def banner():
    print(f"""
{BOLD}{CYAN}╔══════════════════════════════════════════════════════╗
║           ScreenWire AI — Agent Training Mode        ║
╚══════════════════════════════════════════════════════╝{RESET}
{DIM}Talk directly to any pipeline agent. Their full terminal
output is visible to you. Edit their prompts, test tasks,
and refine behavior interactively.{RESET}
""")


def print_agents():
    print(f"  {BOLD}Pipeline Agents:{RESET}\n")
    for i, (agent_id, desc) in enumerate(AGENTS, 1):
        prompt_exists = (PROMPTS_DIR / f"{agent_id}.md").exists()
        status = f"{GREEN}●{RESET}" if prompt_exists else f"{RED}●{RESET}"
        print(f"  {status} {BOLD}[{i}]{RESET}  {desc}")
        print(f"       {DIM}prompt: agent_prompts/{agent_id}.md{RESET}")
    print()


def pick_project() -> Path:
    projects = sorted([d for d in PROJECTS_DIR.iterdir() if d.is_dir()])
    if not projects:
        print(f"{RED}No test projects found in {PROJECTS_DIR}{RESET}")
        sys.exit(1)

    if len(projects) == 1:
        print(f"  {DIM}Project: {projects[0].name}{RESET}")
        return projects[0]

    print(f"  {BOLD}Test Projects:{RESET}\n")
    for i, p in enumerate(projects, 1):
        print(f"    {BOLD}[{i}]{RESET}  {p.name}")
    print()

    while True:
        choice = input(f"  {WHITE}Select project [1]: {RESET}").strip()
        if not choice:
            return projects[0]
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(projects):
                return projects[idx]
        except ValueError:
            # Try matching by name substring
            matches = [p for p in projects if choice.lower() in p.name.lower()]
            if len(matches) == 1:
                return matches[0]
        print(f"  {RED}Invalid choice. Try again.{RESET}")


def pick_agent() -> str:
    print_agents()
    while True:
        choice = input(f"  {WHITE}Select agent [1-{len(AGENTS)}]: {RESET}").strip()
        if not choice:
            continue
        # Accept number
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(AGENTS):
                return AGENTS[idx][0]
        except ValueError:
            pass
        # Accept agent_id or partial match
        matches = [a for a in AGENT_IDS if choice.lower() in a.lower()]
        if len(matches) == 1:
            return matches[0]
        if choice.lower() in AGENT_IDS:
            return choice.lower()
        print(f"  {RED}Invalid choice. Try a number or agent name.{RESET}")


def build_training_preamble(agent_id: str) -> str:
    """Prepend training-mode instructions to the agent's system prompt."""
    return f"""## TRAINING MODE ACTIVE

You are running in **interactive training mode**. This changes your behavior:

1. **DO NOT auto-execute** — Wait for the user's instructions before taking action.
2. **Explain your reasoning** — When the user asks you to do something, explain what you would do and why before doing it. This helps them understand and refine your behavior.
3. **Accept corrections** — If the user says "no, instead do X", adopt that approach.
4. **Show your work** — When reading files or making decisions, narrate what you see and how you interpret it.
5. **Stay in character** — You are still the {agent_id} agent with all your domain knowledge and skills. But you take direction from the user instead of running autonomously.

The user is training you to improve your pipeline performance. Be collaborative and transparent.

---

"""


def spawn_session(agent_id: str, project_dir: Path, model: str) -> int:
    prompt_path = PROMPTS_DIR / f"{agent_id}.md"
    if not prompt_path.exists():
        print(f"{RED}Prompt file not found: {prompt_path}{RESET}")
        return 1

    raw_prompt = prompt_path.read_text()
    # Expand {{include:path}} markers relative to prompt directory
    raw_prompt = _expand_includes(raw_prompt, prompt_path.parent)
    system_prompt = build_training_preamble(agent_id) + raw_prompt

    # Deploy shared conventions as CLAUDE.md if needed
    _deploy_shared_conventions(project_dir)

    env = {
        **os.environ,
        "PROJECT_DIR": str(project_dir),
        "SKILLS_DIR": str(SKILLS_DIR),
    }
    # Prevent nested-session detection
    env.pop("CLAUDECODE", None)
    existing_pythonpath = env.get("PYTHONPATH", "")
    repo_root = str(APP_DIR)
    env["PYTHONPATH"] = repo_root if not existing_pythonpath else f"{repo_root}{os.pathsep}{existing_pythonpath}"

    cmd = [
        sys.executable,
        "-m", "llm.agent_runner",
        "--system-prompt", system_prompt,
        "--model", model,
        "--dangerously-skip-permissions",
        "--task-hint", agent_id,
    ]

    desc = dict(AGENTS).get(agent_id, agent_id)
    print(f"""
{BOLD}{CYAN}{'─' * 56}{RESET}
{BOLD}  Launching: {GREEN}{desc}{RESET}
{BOLD}  Project:   {WHITE}{project_dir.name}{RESET}
{BOLD}  Model:     {WHITE}{model}{RESET}
{BOLD}  Prompt:    {DIM}{prompt_path.relative_to(APP_DIR)}{RESET}
{CYAN}{'─' * 56}{RESET}
{DIM}  You are now talking directly to the {agent_id} agent.
  Type normally to interact. Use /exit or Ctrl+C to end.{RESET}
""")

    # Spawn interactive — stdin/stdout/stderr all go to the real terminal
    result = subprocess.run(cmd, cwd=str(project_dir), env=env)
    return result.returncode


def main():
    parser = argparse.ArgumentParser(
        description="ScreenWire AI — Agent Training Mode",
    )
    parser.add_argument(
        "agent", nargs="?", default=None,
        help="Agent ID to launch directly (e.g. creative_coordinator)",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="List available agents and exit",
    )
    parser.add_argument(
        "--model", default=DEFAULT_MODEL,
        help=f"Model to use (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--project", default=None,
        help="Project directory name or path (skips project picker)",
    )
    args = parser.parse_args()

    banner()

    if args.list:
        print_agents()
        sys.exit(0)

    # Resolve project
    if args.project:
        proj = Path(args.project)
        if not proj.is_absolute():
            proj = PROJECTS_DIR / args.project
        if not proj.is_dir():
            print(f"{RED}Project not found: {proj}{RESET}")
            sys.exit(1)
        project_dir = proj
    else:
        project_dir = pick_project()

    # Resolve agent
    if args.agent:
        agent_id = args.agent.lower().replace("-", "_")
        if agent_id not in AGENT_IDS:
            # Try partial match
            matches = [a for a in AGENT_IDS if agent_id in a]
            if len(matches) == 1:
                agent_id = matches[0]
            else:
                print(f"{RED}Unknown agent: {args.agent}{RESET}")
                print_agents()
                sys.exit(1)
    else:
        agent_id = pick_agent()

    rc = spawn_session(agent_id, project_dir, args.model)

    # After session ends, offer to continue with another agent
    print(f"\n{DIM}Session ended (exit={rc}).{RESET}")
    while True:
        again = input(f"\n  {WHITE}Train another agent? [y/N]: {RESET}").strip().lower()
        if again in ("y", "yes"):
            agent_id = pick_agent()
            rc = spawn_session(agent_id, project_dir, args.model)
            print(f"\n{DIM}Session ended (exit={rc}).{RESET}")
        else:
            break

    print(f"\n{DIM}Training mode complete.{RESET}\n")


if __name__ == "__main__":
    main()
