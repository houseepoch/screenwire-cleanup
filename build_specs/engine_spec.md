# Engine Spec — ScreenWire AI FastAPI Core

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
- Read `FAL_KEY` from env
- Call `POST https://fal.run/fal-ai/flux-pro/v2` with:
  ```json
  {"prompt": "...", "image_size": "landscape_16_9", "num_inference_steps": 28, "guidance_scale": 3.5, "seed": null, "num_images": 1, "output_format": "png", "sync_mode": true}
  ```
- Response: `{"images": [{"url": "https://fal.media/..."}], "seed": 42}`
- Download image from `images[0].url` to `{output_path}.tmp`
- `os.replace()` to final path
- Return `{"success": true, "path": "...", "seed": 42}`
- tenacity: retry 3x with exponential backoff on HTTP 429/500/503

#### POST /internal/generate-image-redux
- Body: `{image_url, prompt, image_size, output_path, strength, seed}`
- Same pattern, call `https://fal.run/fal-ai/flux-pro/v2/redux`

#### POST /internal/design-voice
- Body: `{voice_description, text, model_id, guidance_scale}`
- Read `ELEVENLABS_API_KEY` from env
- Call `POST https://api.elevenlabs.io/v1/text-to-voice/design` with header `xi-api-key`
- Body: `{"voice_description": "...", "model_id": "eleven_ttv_v3", "text": "...", "auto_generate_text": false, "loudness": 0.5, "guidance_scale": 5}`
- Return full response JSON (previews array)

#### POST /internal/save-voice
- Body: `{voice_name, voice_description, generated_voice_id, labels}`
- Call `POST https://api.elevenlabs.io/v1/text-to-voice`
- Return `{"voice_id": "...", "name": "..."}`

#### POST /internal/generate-tts
- Body: `{voice_id, text, model_id, voice_settings, output_path, output_format}`
- Call `POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=mp3_44100_128`
- With header `xi-api-key` and body: `{"text": "...", "model_id": "eleven_v3", "voice_settings": {...}}`
- **Response is RAW binary audio/mpeg** — write directly to file (not JSON!)
- Capture `request-id` response header
- Return `{"success": true, "path": "...", "request_id": "..."}`

#### POST /internal/generate-dialogue
- Body: `{inputs, model_id, settings, output_path}`
- Call `POST https://api.elevenlabs.io/v1/text-to-dialogue/with-timestamps?output_format=mp3_44100_128`
- Body: `{"inputs": [{"text": "...", "voice_id": "..."}], "model_id": "eleven_v3", "settings": {"stability": 0.5}}`
- Response is JSON with `audio_base64` field — base64 decode and write to file
- Return full response JSON + saved path

#### POST /internal/generate-video
- Body: `{model, prompt, image_path, audio_path, duration, resolution, output_path, extra_params}`
- Read `REPLICATE_API_TOKEN` from env
- Upload local files to Replicate: `POST https://api.replicate.com/v1/files` (multipart)
- Create prediction: `POST https://api.replicate.com/v1/predictions` with `Authorization: Bearer {token}` and `Prefer: wait`
- For p-video (prunaai/p-video): `{"model": "prunaai/p-video", "input": {"prompt": "...", "image": "{url}", "audio": "{url}", "resolution": "720p", "fps": 24, "save_audio": true, "draft": false, "prompt_upsampling": true}}`
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
