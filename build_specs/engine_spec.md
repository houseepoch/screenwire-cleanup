# Engine Spec — ScreenWire AI FastAPI Core

> Legacy planning document. Server endpoints for external voice design, TTS,
> dialogue generation, and p-video routing are retired from the active runtime.

## Task
Build the FastAPI backend engine for the headless MVP pipeline.

## Files to Create

### $APP_DIR/requirements.txt
```
fastapi
uvicorn[standard]
watchdog
tenacity
python-dotenv
httpx
aiofiles
pydantic
```

### $APP_DIR/server.py

Single-file FastAPI server. Headless — no UI, no WebSocket. Port 8000.

## Architecture

### Startup
- Load .env from $APP_DIR/.env
- PROJECT_DIR env var or default to: test_project/sw_test001_greenhouse-letter/ (relative to app dir)
- Start ManifestReconciler + Sentinel as lifespan background tasks
- Register atexit + signal handlers for graceful shutdown

### Module A: ManifestReconciler

Single-writer pattern for project_manifest.json.

1. **In-memory state**: Load manifest JSON on startup into a dict. All reads/writes go through this dict.

2. **Watchdog Observer**: Monitor `{project_dir}/dispatch/manifest_queue/` for new .json files.

3. **On new file detected**:
   - Read file content
   - Strip markdown code fences: `re.sub(r'^```\w*\n|\n```$', '', text.strip())`
   - Try `json.loads()` — on failure: move file to `dead_letters/` subdir, log error
   - On success: push parsed dict to `asyncio.Queue`, delete the source file

4. **Writer loop** (background task):
   - Pop from queue one at a time
   - Apply updates to in-memory manifest:
     - Update format: `{"updates": [{"target": "frame", "frameId": "f_001", "set": {"field": "value"}}]}`
     - `target=frame`: find item in `manifest["frames"]` where `frameId` matches, merge `set` dict
     - `target=cast`: find by `castId` in `manifest["cast"]`
     - `target=location`: find by `locationId` in `manifest["locations"]`
     - `target=prop`: find by `propId` in `manifest["props"]`
     - `target=dialogue`: load dialogue.json, find by `dialogueId`, merge set, write back
     - `target=phase`: update `manifest["phases"][phaseId]` with set dict
     - `target=project`: merge set into top-level manifest
   - Increment `manifest["version"]`
   - Write to `project_manifest.json.tmp`
   - `os.replace()` to `project_manifest.json` (atomic)

### Module B: Sentinel (simplified for MVP)

- Watchdog Observer on: `video/clips/`, `frames/composed/`, `audio/dialogue/`, `dispatch/flags/`
- On file creation: print timestamped log message. Skip `.tmp` files and zero-byte files.
- No WebSocket. No stall detector. Just logging.

### Module C: Agent Process Manager

```python
class AgentProcessManager:
    registry: dict[str, asyncio.subprocess.Process]

    async def spawn_agent(agent_id, system_prompt, cwd, model="claude-opus-4-6") -> Process
    # Run: claude -p "{system_prompt}" --dangerously-skip-permissions --output-format stream-json --model {model}
    # stdin=PIPE, stdout=PIPE, stderr=PIPE
    # Register in self.registry

    async def send_directive(agent_id, message)
    # proc.stdin.write(f"{message}\n".encode()); await proc.stdin.drain()

    async def kill_agent(agent_id)
    # SIGTERM, wait 5s, SIGKILL if still alive

    async def kill_all()
    # Kill everything

    def get_status(agent_id) -> str
    # "alive" or "dead" based on proc.returncode
```

Graceful shutdown: atexit handler + SIGINT/SIGTERM signal handler calls kill_all().

### Layer 1: Programmatic Gateways

Internal HTTP endpoints. Each handles API keys, retries (tenacity), atomic file writes.

Use `httpx.AsyncClient` for all external API calls.

#### POST /internal/generate-image
- Body: `{prompt, image_size, output_path, num_inference_steps, guidance_scale, seed, output_format}`

#### POST /internal/generate-video
- Body: `{model, prompt, image_path, audio_path, duration, resolution, output_path, extra_params}`
- Read `REPLICATE_API_TOKEN` from env
- Upload local files to Replicate: `POST https://api.replicate.com/v1/files` (multipart)
- Create prediction: `POST https://api.replicate.com/v1/predictions` with `Authorization: Bearer {token}` and `Prefer: wait`
- For grok-video (xai/grok-imagine-video): `{"model": "xai/grok-imagine-video", "input": {"prompt": "...", "image": "{url}", "duration": 5, "resolution": "720p", "aspect_ratio": "auto"}}`
- If response status != "succeeded", poll `GET /v1/predictions/{id}` every 5s until done
- Download output MP4 to `{output_path}.tmp`, os.replace
- Return `{"success": true, "path": "...", "prediction_id": "..."}`

#### POST /internal/upload-to-replicate
- Multipart file upload to `https://api.replicate.com/v1/files`
- Return `{"url": "..."}`

### Public API Routes

- `GET /api/project/current` — return current manifest JSON from memory
- `GET /health` — return `{"status": "ok"}`

## Implementation Notes
- Single file: server.py (it's an MVP)
- Use `from contextlib import asynccontextmanager` for FastAPI lifespan
- httpx.AsyncClient as app-level singleton
- tenacity `@retry` on gateway functions: `retry=retry_if_exception_type(httpx.HTTPStatusError), wait=wait_exponential(min=2, max=30), stop=stop_after_attempt(3)`
- Actually check the status code in retry — only retry on 429, 500, 503
- Print-based logging: `print(f"[{datetime.now().isoformat()}] [MODULE] message")`
- All paths resolve relative to PROJECT_DIR
- Make sure to create parent dirs for output files with os.makedirs(exist_ok=True)
