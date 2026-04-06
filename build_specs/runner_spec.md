# Pipeline Runner Spec — ScreenWire AI Headless MVP

## Task
Build a Python script that drives the full pipeline from Phase 0→6 without any human input. This is the test harness that proves the pipeline works end-to-end.

## Location
$APP_DIR/run_pipeline.py

## What It Does

The runner is a sequential Python script that:
1. Starts the FastAPI server as a subprocess
2. Waits for server to be ready (poll /health)
3. Runs through each phase, spawning Claude CLI agents with the right system prompts
4. Auto-approves all gates
5. Monitors agent completion by polling state files
6. Advances phases
7. Runs Phase 6 (export) programmatically via ffmpeg
8. Reports results

## Dependencies
- The FastAPI server (server.py) must be running on port 8000
- Agent prompts must exist at $APP_DIR/agent_prompts/
- Test project must be scaffolded at test_project/sw_test001_greenhouse-letter/
- .env must exist with API keys
- claude CLI must be available on PATH

## Implementation

```python
#!/usr/bin/env python3
"""ScreenWire AI — Headless Pipeline Runner (MVP Test Harness)"""
```

### Configuration
```python
APP_DIR = "$APP_DIR"
PROJECT_DIR = f"{APP_DIR}/test_project/sw_test001_greenhouse-letter"
PROMPTS_DIR = f"{APP_DIR}/agent_prompts"
SKILLS_DIR = f"{APP_DIR}/skills"
SERVER_URL = "http://localhost:8000"
```

### Phase Flow

#### Phase 0 — Already Complete
The scaffold agent already created onboarding_config.json and set phase_0 to complete. Runner just verifies.

#### Phase 1 — Narrative (Creative Writing)
1. Read director.md prompt, spawn Director Claude CLI session
   - Director reads source material, writes project_brief.md
   - Director auto-approves (MVP)
2. Read creative_coordinator.md prompt, spawn CC Claude CLI session
   - CC writes skeleton, scene outlines, scene drafts, creative_output.md
   - CC updates state to "awaiting_review" after each sub-phase
3. Runner polls CC state.json — when "awaiting_review", write a proceed directive to CC's directive.json and signal it
4. After CC completes all 3 sub-phases and writes creative_output.md:
   - Update manifest: phase_1 complete, phase_2 ready
   - Kill CC session

For MVP simplification: Don't actually use Director to review CC's work. Just let CC run through all 3 sub-phases autonomously, then advance. The Director prompt review step can be done by reading the output after the fact.

SIMPLER APPROACH: Spawn CC with a prompt that tells it to do all 3 sub-phases in one go without waiting for review. This avoids the polling/directive complexity.

#### Phase 2 — Staging (Decomposition)
1. Spawn Decomposer with its prompt
2. Wait for completion (poll state.json or wait for process exit)
3. Verify outputs exist: dialogue.json, cast/*.json, locations/*.json, props/*.json, manifest has frames[]
4. Update manifest: phase_2 complete, phase_3 ready

#### Phase 3 — Visual & Voice Assets
1. Spawn Scene Coordinator — generates images
2. Spawn Voice Director — creates voices (can run in parallel with SC, but sequential is fine for MVP)
3. Wait for both to complete
4. Verify: all cast have composites, all locations have primaries, all props have images, all speaking chars have voice_ids
5. Update manifest: phase_3 complete, phase_4 ready

#### Phase 4 — Production Coordination
1. Spawn Production Coordinator
2. Wait for completion
3. Verify: all dialogue has audio, all frames have composed images, timeline.json exists
4. Update manifest: phase_4 complete, phase_5 ready

#### Phase 5 — Video Generation
1. Spawn Video Agent
2. Wait for completion (this is the longest phase — could take 20-60 mins)
3. Verify: all frames have video clips
4. Update manifest: phase_5 complete, phase_6 ready

#### Phase 6 — Export (Programmatic, No Agent)
1. Read manifest frames in timeline order
2. Normalize all clips via ffmpeg (libx264, 24fps, 1280x720, aac 48khz stereo)
3. Write concat_list.txt
4. ffmpeg concat demuxer with -c copy
5. ffmpeg loudnorm pass (-16 LUFS)
6. ffprobe verification
7. Update manifest: project complete

### Agent Spawning

For MVP, spawn agents as simple subprocess calls:

```python
import subprocess

def run_agent(agent_id: str, prompt_file: str, project_dir: str, model: str = "claude-opus-4-6") -> subprocess.CompletedProcess:
    """Spawn a Claude CLI agent and wait for it to finish."""
    with open(prompt_file, 'r') as f:
        system_prompt = f.read()

    # Set environment so skills know the project dir
    env = {**os.environ, "PROJECT_DIR": project_dir}

    result = subprocess.run(
        ["claude", "-p", system_prompt,
         "--dangerously-skip-permissions",
         "--output-format", "stream-json",
         "--model", model],
        cwd=project_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=1800  # 30 min timeout per agent
    )
    return result
```

This is simpler than using the server's Agent Process Manager. For MVP, we just want to prove the pipeline works. The server's APM will be used in the full build.

### Phase Advancement

After each agent completes, the runner:
1. Reads project_manifest.json
2. Updates the phase status
3. Writes the updated manifest back
4. Prints a summary of what was produced

### Monitoring Output

For each agent run, the runner should:
- Print the agent name and phase being executed
- Print timing (start/end/duration)
- Print a summary of files created (ls the relevant output dirs)
- On agent failure: print stderr, print last 50 lines of the agent's events.jsonl if it exists

### Error Handling

- If an agent fails (non-zero exit, timeout): log the error, print agent output, and STOP. Don't try to continue to the next phase.
- If a verification step fails (expected files don't exist): log what's missing and STOP.

### The Script Should Be Runnable As:

```bash
cd "$APP_DIR"
# Start server in background first:
# python3 server.py &
# Then run pipeline:
python3 run_pipeline.py
```

Or it can start the server itself as a subprocess.

### Output

When complete, print:
- Total pipeline duration
- Per-phase duration breakdown
- Files created count per phase
- Final export path
- Any warnings or issues encountered

## Important Notes
- The runner does NOT use the Agent Process Manager in server.py — it spawns claude directly via subprocess for simplicity
- The server still needs to be running for the skills (sw_generate_image etc.) to work, since they call localhost:8000
- Agent prompts must tell agents to run skills using the full path: python3 {SKILLS_DIR}/skill_name
- Set PROJECT_DIR env var so skills know where the project is
- The runner should start the server automatically if it's not already running
- Include a --dry-run flag that prints the plan without executing
- Include a --phase flag to run a single specific phase (for debugging)
