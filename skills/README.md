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

POSTs to `/internal/fresh-generation` using the Nano Banana generation chain.

```
./sw_generate_image --prompt "A greenhouse at golden hour" --out assets/frame_01.png
```

| Arg | Required | Default | Description |
|---|---|---|---|
| `--prompt` | yes | — | Generation prompt |
| `--out` | yes | — | Output path |
| `--size` | no | `landscape_16_9` | Image size preset |
| `--reference-image` | no | — | Optional reference image path (repeatable) |
| `--image-search` | no | `false` | Enable image grounding |
| `--google-search` | no | `false` | Enable web grounding |

---

## `sw_edit_image`

POSTs to `/internal/edit-image` using the Nano Banana edit chain.

```
./sw_edit_image --input assets/frame_01.png --prompt "make the coat red" --out assets/frame_01_edit.png
```

| Arg | Required | Description |
|---|---|---|
| `--input` | yes | Source image path |
| `--prompt` | yes | Edit instruction |
| `--out` | yes | Output path |

---

## `sw_query_graph_database`

Friendly wrapper around `graph_query` for database-style graph lookups.

```
./sw_query_graph_database --type frame --filter '{"scene_id":"scene_04"}'
./sw_query_graph_database --frame-context f_014
```

---

## `sw_grep_research`

Searches project and support text files for matches.

```
./sw_grep_research --pattern "dialogue density" --path reports
```

| Arg | Required | Description |
|---|---|---|
| `--pattern` | yes | Regex pattern |
| `--path` | no | Relative search root inside project |
| `--max-results` | no | Max matches to print |

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

## `sw_extend_video`

POSTs to `/internal/extend-video` using Grok video extension on Replicate.

```
./sw_extend_video --video render/clip_01.mp4 --prompt "continue the slow dolly and keep the same mood" --out render/clip_01_ext.mp4
```

| Arg | Required | Description |
|---|---|---|
| `--video` | yes | Source video file |
| `--out` | yes | Output video path |
| `--prompt` | no | Optional extension instruction |
| `--duration` | no | Extension duration in seconds |

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
