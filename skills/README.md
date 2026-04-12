# ScreenWire AI — Active CLI Skills

This file documents the supported top-level skill surface for the current runtime.
The active pipeline uses a single native-audio video path: dialogue and ambience
are delivered inside the structured `AUDIO:` section of each Grok video prompt.

---

## `sw_read_manifest`

Reads `/api/project/current` and prints a condensed project summary.

```
./sw_read_manifest
```

---

## `sw_queue_update`

Writes a manifest micro-update JSON to
`{PROJECT_DIR}/dispatch/manifest_queue/micro-update_{epoch_ms}.json`.

```
./sw_queue_update --payload '{"key": "value"}'
```

| Arg | Required | Description |
|---|---|---|
| `--payload` | yes | JSON string; markdown fences are stripped automatically |

---

## `sw_update_state`

Reads `logs/{agent}/state.json`, merges new fields, and writes it back.

```
./sw_update_state --agent director --status done --sub-phase review
```

| Arg | Required | Description |
|---|---|---|
| `--agent` | yes | Agent ID |
| `--status` | yes | New status value |
| `--sub-phase` | no | Optional sub-phase string |
| `--file` | no | Override output path |

---

## `sw_generate_image`

POSTs to `/internal/generate-image`.

```
./sw_generate_image --prompt "A greenhouse at golden hour" --out assets/frame_01.png
```

| Arg | Required | Default | Description |
|---|---|---|---|
| `--prompt` | yes | — | Generation prompt |
| `--out` | yes | — | Output path |
| `--size` | no | `landscape_16_9` | Image size preset |
| `--steps` | no | `28` | Inference steps |
| `--guidance` | no | `3.5` | Guidance scale |

---

## `sw_generate_video`

POSTs to `/internal/generate-video` using the single active path: Grok
native-audio video. Spoken dialogue and ambience belong in the prompt's
structured `AUDIO:` section.

```
./sw_generate_video --image assets/frame_01.png --prompt "slow push with native dialogue" --out render/clip_01.mp4
```

| Arg | Required | Description |
|---|---|---|
| `--image` | no | Input image path |
| `--prompt` | yes | Structured motion + native-audio prompt |
| `--out` | yes | Output path |
| `--duration` | no | Optional duration in seconds |

---

## `skill_extract_last_frame`

Extracts the last frame of a video using `ffmpeg`.

```
./skill_extract_last_frame --video render/clip_01.mp4 --out assets/last_frame.png
```

| Arg | Required | Description |
|---|---|---|
| `--video` | yes | Input video file |
| `--out` | yes | Output image file |

---

## `skill_verify_media`

Runs `ffprobe` on a media file and prints formatted metadata.

```
./skill_verify_media --file render/clip_01.mp4
```

| Arg | Required | Description |
|---|---|---|
| `--file` | yes | Media file to inspect |

---

## `sw_migrate_prompt_size_keys`

Audits prompt JSON artifacts and normalizes legacy `image_size` keys to the
current `size` schema. Default mode is audit-only; add `--apply` to rewrite.

```
./sw_migrate_prompt_size_keys --roots projects --report logs/prompt_size_audit.json
./sw_migrate_prompt_size_keys --roots projects --apply
```

| Arg | Required | Description |
|---|---|---|
| `--roots` | no | One or more directories to scan recursively |
| `--apply` | no | Rewrite legacy prompt JSONs in place |
| `--report` | no | Optional JSON summary output path |

---

## Graph Utility Skills

The runtime also depends on the graph-oriented utility scripts such as
`graph_materialize`, `graph_assemble_prompts`, `graph_generate_assets`,
`graph_build_grids`, `graph_sync_assets`, and related validation helpers.
These operate on the canonical graph and prompt pipeline rather than on
standalone audio subsystems.
