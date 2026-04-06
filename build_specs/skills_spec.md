# Skills Spec — ScreenWire AI Layer 2 CLI Scripts

## Task
Build 13 CLI skill scripts that agents call from the command line.

## Location
All scripts in: $APP_DIR/skills/

## Common Rules
- `#!/usr/bin/env python3` shebang
- `chmod +x` each script after writing
- argparse for CLI args
- Stdout output: `SUCCESS: ...` or `ERROR: ...`
- Zero retry logic (backend handles retries)
- Strip markdown fences from JSON: `re.sub(r'^```\w*\n|\n```$', '', text.strip())`
- httpx sync client for HTTP calls to localhost:8000
- Read PROJECT_DIR from env, default: `$APP_DIR/test_project/sw_test001_greenhouse-letter`
- Resolve relative paths to absolute using PROJECT_DIR
- Each script has `if __name__ == "__main__"` block

## Scripts

### 1. sw_read_manifest
No args. GET http://localhost:8000/api/project/current. Print condensed summary: project name, status, phase statuses, cast/location/prop/frame counts, frame status breakdown.

### 2. sw_queue_update
`--payload` (JSON string). Strip fences. Write JSON to `{PROJECT_DIR}/dispatch/manifest_queue/micro-update_{timestamp}.json`. No HTTP call. Timestamp format: epoch millis.

### 3. sw_update_state
`--agent` (agent_id), `--status`, `--sub-phase` (optional), `--file` (optional output path). Read existing state.json at `logs/{agent}/state.json`, merge new fields, write back. Create file if doesn't exist.

### 4. sw_generate_image
`--prompt`, `--out` (relative path), `--size` (optional, default "landscape_16_9"), `--steps` (optional, default 28), `--guidance` (optional, default 3.5). POST http://localhost:8000/internal/generate-image. Body: `{"prompt": "...", "image_size": "...", "output_path": "{absolute}", "num_inference_steps": 28, "guidance_scale": 3.5, "output_format": "png"}`.

### 5. sw_generate_tts
`--voice-id`, `--text`, `--out` (relative), `--model` (optional "eleven_v3"), `--stability` (optional 0.5), `--similarity` (optional 0.75). POST /internal/generate-tts. Body: `{"voice_id": "...", "text": "...", "model_id": "...", "voice_settings": {"stability": 0.5, "similarity_boost": 0.75, "style": 0.0, "speed": 1.0}, "output_path": "{abs}", "output_format": "mp3_44100_128"}`.

### 6. sw_generate_video
`--api` (p-video|grok-video), `--image` (relative), `--prompt`, `--out` (relative), `--audio` (optional relative), `--duration` (optional int). POST /internal/generate-video. Map: p-video -> prunaai/p-video, grok-video -> xai/grok-imagine-video. Resolve all paths.

### 7. sw_design_voice
`--description`, `--text`, `--guidance` (optional, default 5). POST /internal/design-voice. Decode base64 preview audio and save to `{PROJECT_DIR}/logs/voice_director/previews/`. Print count and IDs.

### 8. sw_save_voice
`--name`, `--description`, `--generated-voice-id`, `--labels` (optional JSON string). POST /internal/save-voice. Print permanent voice_id.

### 9. sw_generate_dialogue
`--inputs` (JSON string `[{"text":"...","voice_id":"..."}]`), `--out` (relative), `--stability` (optional 0.5). Strip fences from inputs. POST /internal/generate-dialogue. Save audio + write segments JSON to `{out_stem}_segments.json`.

### 10. skill_slice_audio
`--input`, `--start` (float), `--end` (float), `--out`. Run: `ffmpeg -y -i {input} -ss {start} -to {end} -c:a libmp3lame -b:a 128k {out}`

### 11. skill_generate_silence
`--duration` (float), `--out`. Run: `ffmpeg -y -f lavfi -i anullsrc=r=44100:cl=stereo -t {duration} -c:a libmp3lame -b:a 128k {out}`

### 12. skill_extract_last_frame
`--video`, `--out`. Run: `ffmpeg -y -sseof -0.04 -i {video} -frames:v 1 {out}`

### 13. skill_verify_media
`--file`. Run: `ffprobe -v error -show_entries format=duration,size,bit_rate -show_entries stream=codec_name,width,height,r_frame_rate -of json {file}`. Print formatted results.

## Also Create
- `skills/__init__.py` (empty)
- `skills/README.md` listing all 13 skills with their args and descriptions
