# ScreenWire AI — API Reference (Single Source of Truth)

*Last updated: 2026-04-01*

This document is the authoritative reference for all external API integrations used by ScreenWire AI. All agents, skills, and gateways MUST conform to this document.

---

## Authentication

Only **two API keys** are required:

| Key | Environment Variable | Used By |
|-----|---------------------|---------|
| Replicate | `REPLICATE_API_TOKEN` | Reference images (p-image), Frame generation (Flux 2 Pro), Video (p-video, grok-video) |
| ElevenLabs | `ELEVENLABS_API_KEY` | Voice design, TTS, Dialogue generation |

`.env` file:
```
REPLICATE_API_TOKEN=your_token
ELEVENLABS_API_KEY=your_key
```

---

## 1A. REFERENCE IMAGE GENERATION — Replicate p-image

### Model: `prunaai/p-image`

Sub-1 second text-to-image model for reference assets (mood boards, cast composites, location refs, prop refs). Used in Phase 3 by Scene Coordinator.

**Endpoint (Official Model — no version ID needed):**
```
POST https://api.replicate.com/v1/models/prunaai/p-image/predictions
Authorization: Bearer {REPLICATE_API_TOKEN}
Content-Type: application/json
Prefer: wait
```

**Request Body:**
```json
{
  "input": {
    "prompt": "...",
    "aspect_ratio": "16:9",
    "disable_safety_checker": true
  }
}
```

**Input Parameters:**
| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `prompt` | string | required | Text prompt |
| `aspect_ratio` | enum | `"16:9"` | `1:1`, `16:9`, `9:16`, `4:3`, `3:4`, `3:2`, `2:3`, `custom` |
| `width` | int | null | Only when aspect_ratio=custom. 256-1440, multiple of 16 |
| `height` | int | null | Only when aspect_ratio=custom. 256-1440, multiple of 16 |
| `seed` | int | null | Random seed for reproducibility |
| `disable_safety_checker` | bool | false | Disable safety filter |
| `prompt_upsampling` | bool | false | LLM-enhanced prompt |

**Output:** Single URL string (JPEG)

**ScreenWire Wrapper:** `POST /internal/generate-image` → `sw_generate_image` skill

---

## 1B. FRAME GENERATION — Replicate Flux 2 Pro

### Model: `black-forest-labs/flux-2-pro`

High-quality frame composition for scene frames. Used in Phase 4 by Production Coordinator.

**Endpoint (Official Model — no version ID needed):**
```
POST https://api.replicate.com/v1/models/black-forest-labs/flux-2-pro/predictions
Authorization: Bearer {REPLICATE_API_TOKEN}
Content-Type: application/json
```

**Request Body:**
```json
{
  "input": {
    "prompt": "Detailed scene description...",
    "aspect_ratio": "16:9",
    "resolution": "1 MP",
    "seed": null,
    "safety_tolerance": 2,
    "output_format": "png",
    "output_quality": 80
  }
}
```

Add `Prefer: wait` header for synchronous response (blocks until image is ready).

**Parameters:**

| Parameter | Type | Default | Options |
|-----------|------|---------|---------|
| `prompt` | string | *required* | Text description |
| `input_images` | array of URIs | `[]` | Up to 8 reference images (for image-to-image / consistency) |
| `aspect_ratio` | enum | `1:1` | `1:1`, `16:9`, `9:16`, `3:2`, `2:3`, `4:5`, `5:4`, `3:4`, `4:3`, `custom`, `match_input_image` |
| `resolution` | enum | `1 MP` | `0.5 MP`, `1 MP`, `2 MP`, `4 MP` |
| `width` | integer | null | 256-2048 (multiples of 16, only for `custom` aspect ratio) |
| `height` | integer | null | 256-2048 (multiples of 16, only for `custom` aspect ratio) |
| `seed` | integer | random | For reproducibility |
| `safety_tolerance` | integer | `2` | 1 (strict) to 5 (permissive) |
| `output_format` | enum | `webp` | `webp`, `jpg`, `png` |
| `output_quality` | integer | `80` | 0-100 |

**Response (succeeded):**
```json
{
  "id": "prediction_abc123",
  "model": "black-forest-labs/flux-2-pro",
  "status": "succeeded",
  "output": "https://replicate.delivery/xyzfile.png",
  "metrics": {"predict_time": 3.2},
  "urls": {
    "get": "https://api.replicate.com/v1/predictions/prediction_abc123"
  }
}
```

**CRITICAL:** The `output` URL is temporary (~1-24 hours). Download immediately and save to local canonical path.

**Pricing:** ~$0.055 per image

### Image-to-Image / Redux

Use the same `black-forest-labs/flux-2-pro` model with `input_images` parameter:
```json
{
  "input": {
    "prompt": "Same character in a different pose...",
    "input_images": ["https://replicate.delivery/character_ref.png"],
    "aspect_ratio": "16:9",
    "output_format": "png"
  }
}
```

For dedicated editing/inpainting, use:
- `black-forest-labs/flux-kontext-pro` — text-guided image editing ($0.04/image)
- `black-forest-labs/flux-fill-pro` — inpainting with mask

---

## 2. VIDEO GENERATION — Replicate

### 2a. Dialogue Frames: `prunaai/p-video`

For frames WITH dialogue audio (lip-sync). Formula tags F04, F05, F06.

**Endpoint:**
```
POST https://api.replicate.com/v1/models/prunaai/p-video/predictions
Authorization: Bearer {REPLICATE_API_TOKEN}
Content-Type: application/json
```

**Request Body:**
```json
{
  "input": {
    "prompt": "Close-up dialogue shot. Sarah speaks earnestly...",
    "image": "https://replicate.delivery/composed_frame.png",
    "audio": "https://replicate.delivery/dialogue.mp3",
    "resolution": "720p",
    "fps": 24,
    "draft": false,
    "prompt_upsampling": true,
    "save_audio": true,
    "seed": null
  }
}
```

**Parameters:**

| Parameter | Type | Default | Notes |
|-----------|------|---------|-------|
| `prompt` | string | *required* | Motion/animation description |
| `image` | URI | null | Composed frame PNG as first frame |
| `last_frame_image` | URI | null | Reference for final frame (optional) |
| `audio` | URI | null | Dialogue MP3 for lip-sync |
| `duration` | integer | `5` | 1-20 seconds. **IGNORED when audio provided** (clip matches audio length) |
| `resolution` | enum | `720p` | `720p`, `1080p` |
| `aspect_ratio` | enum | `16:9` | Ignored when image provided (inherits from image) |
| `fps` | integer | `24` | `24` or `48` |
| `draft` | boolean | `false` | 4x faster, lower quality |
| `prompt_upsampling` | boolean | `true` | Auto-enhance prompt |
| `save_audio` | boolean | `true` | Include audio in output MP4 |
| `seed` | integer | null | For reproducibility |
| `disable_safety_filter` | boolean | `true` | Skip safety checks |

**Key Behaviors:**
- When `audio` is provided, `duration` is IGNORED — clip duration matches audio length
- When `image` is provided, `aspect_ratio` is IGNORED — inherits from image
- Audio formats: flac, mp3, wav (must be HTTPS URL)
- Image formats: jpg, jpeg, png, webp (must be HTTPS URL)
- Max duration: **20 seconds** per call. Longer audio requires chunking.
- Lip-sync is automatic from audio input

**Pricing:** $0.02/sec at 720p, $0.04/sec at 1080p. Draft: $0.005/sec at 720p.

**Response:**
```json
{
  "id": "prediction_xyz",
  "status": "succeeded",
  "output": "https://replicate.delivery/video.mp4"
}
```

### 2b. Non-Dialogue Frames: `xai/grok-imagine-video`

For frames WITHOUT dialogue. Generates native audio (diegetic sounds). All formula tags except F04/F05/F06.

**Endpoint:**
```
POST https://api.replicate.com/v1/models/xai/grok-imagine-video/predictions
Authorization: Bearer {REPLICATE_API_TOKEN}
Content-Type: application/json
```

**Request Body:**
```json
{
  "input": {
    "prompt": "Wide establishing shot. Camera pans slowly... AUDIO: soft wind, birdsong, gentle leaf rustle",
    "image": "https://replicate.delivery/composed_frame.png",
    "duration": 5,
    "resolution": "720p",
    "aspect_ratio": "auto"
  }
}
```

**Parameters:**

| Parameter | Type | Default | Notes |
|-----------|------|---------|-------|
| `prompt` | string | *required* | Motion description + `AUDIO:` soundscape section |
| `image` | URI | null | Composed frame PNG as first frame |
| `duration` | integer | `5` | 1-15 seconds |
| `resolution` | enum | `720p` | `720p`, `480p` |
| `aspect_ratio` | enum | `auto` | `auto` (inherits from image), `16:9`, `9:16`, `1:1`, etc. |

**Key Behaviors:**
- Max duration: **15 seconds** per call
- No `audio` input — audio is generated natively from the `AUDIO:` prompt section
- **Negative prompts are IGNORED.** Always describe what you WANT, not what you don't.
- No seed parameter — non-reproducible
- No FPS parameter — model default

**Pricing:** $0.05/sec at 720p. Example: 5s clip = $0.25.

### 2c. Replicate File Upload

Both video APIs require HTTPS URLs for inputs. Upload local files first:

```
POST https://api.replicate.com/v1/files
Authorization: Bearer {REPLICATE_API_TOKEN}
Content-Type: multipart/form-data

file: {binary data}
content-type: image/png (or audio/mpeg)
```

**Response:**
```json
{
  "id": "file_id",
  "urls": {
    "get": "https://replicate.delivery/..."
  }
}
```

Use `urls.get` as the input URL for prediction calls.

### 2d. Replicate Async Polling

If not using `Prefer: wait`, predictions return immediately with `"status": "starting"`. Poll:

```
GET https://api.replicate.com/v1/predictions/{prediction_id}
Authorization: Bearer {REPLICATE_API_TOKEN}
```

Poll every 5 seconds until `status` is `"succeeded"` or `"failed"`.

**Rate Limits:** 600 predictions/minute. Video Agent limits itself to 3 concurrent predictions.

---

## 3. VOICE DESIGN — ElevenLabs

### 3a. Generate Voice Previews

```
POST https://api.elevenlabs.io/v1/text-to-voice/design
xi-api-key: {ELEVENLABS_API_KEY}
Content-Type: application/json
```

**Request Body:**
```json
{
  "voice_description": "Studio-quality voice-over booth recording... Female, early 30s, low register...",
  "model_id": "eleven_ttv_v3",
  "text": "Sample text 100-1000 chars",
  "auto_generate_text": false,
  "loudness": 0.5,
  "guidance_scale": 5,
  "seed": null
}
```

**Response:**
```json
{
  "previews": [
    {
      "audio_base_64": "{base64 MP3}",
      "generated_voice_id": "preview_abc123",
      "media_type": "audio/mpeg",
      "duration_secs": 4.2
    }
  ],
  "text": "The sample text used"
}
```

Returns ~3 previews. `generated_voice_id` is **temporary** — only usable for saving via 3b.

**Audio quality prefixes by media type:**

| Media Type | Prefix |
|------------|--------|
| `anime` | "Studio-quality voice-over booth recording, clean and isolated, no room ambience, close microphone placement, consistent with professional anime dubbing." |
| `2d_cartoon` | "Studio-quality voice-over booth recording, clean and isolated, no room ambience, close microphone placement, consistent with professional animation voice-over." |
| `3d_animation` | "Studio-quality voice-over booth recording, clean and isolated, no room ambience, close microphone placement, consistent with professional animated feature voice capture." |
| `live_action` | "Clean studio recording with subtle natural room presence, warm and grounded, sounds like professional actors recorded together in a controlled environment, consistent acoustic space." |
| `realistic_3d` | "Clean studio recording with subtle natural room presence, warm and grounded, sounds like professional actors in a controlled acoustic environment, slight natural reverb." |
| `mixed_reality` | "Clean studio recording, neutral acoustic space, no strong room character, balanced between intimate voice-over and natural room presence." |

### 3b. Save Voice (Permanent)

```
POST https://api.elevenlabs.io/v1/text-to-voice
xi-api-key: {ELEVENLABS_API_KEY}
Content-Type: application/json
```

**Request Body:**
```json
{
  "voice_name": "ScreenWire_sw_test001_cast_001_sarah",
  "voice_description": "Studio-quality... Female, early 30s...",
  "generated_voice_id": "preview_abc123",
  "labels": {
    "project": "sw_test001",
    "character": "Sarah",
    "castId": "cast_001_sarah",
    "mediaType": "anime"
  }
}
```

**Response:** Returns `voice_id` (permanent). Use this for ALL future TTS calls.

### 3c. Voice Remix

```
POST https://api.elevenlabs.io/v1/text-to-voice/{voice_id}/remix
xi-api-key: {ELEVENLABS_API_KEY}
Content-Type: application/json
```

Same request format as 3a. Returns previews. Save chosen preview via 3b for new permanent `voice_id`.

---

## 4. TEXT-TO-SPEECH — ElevenLabs

### 4a. Single Voice TTS

```
POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=mp3_44100_128
xi-api-key: {ELEVENLABS_API_KEY}
Content-Type: application/json
```

**Request Body:**
```json
{
  "text": "[controlled, masking urgency] James. I need you to come see something.",
  "model_id": "eleven_v3",
  "voice_settings": {
    "stability": 0.5,
    "similarity_boost": 0.75,
    "style": 0.0,
    "speed": 1.0,
    "use_speaker_boost": true
  },
  "previous_text": null,
  "next_text": null,
  "previous_request_ids": [],
  "seed": null
}
```

**CRITICAL:** `model_id` MUST be `"eleven_v3"` for bracket notation. Default is `eleven_multilingual_v2` which IGNORES brackets.

**Response:** Raw binary audio (`audio/mpeg`). Write directly to file.

**Response Header:** `request-id` — capture for stitching.

### 4b. TTS with Timestamps

```
POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps?output_format=mp3_44100_128
```

Same request body. Response is JSON:
```json
{
  "audio_base64": "{base64 audio}",
  "alignment": {
    "characters": ["H", "e", "l", "l", "o"],
    "character_start_times_seconds": [0.0, 0.05, 0.12, ...],
    "character_end_times_seconds": [0.05, 0.12, 0.18, ...]
  },
  "normalized_alignment": { "..." }
}
```

### 4c. Bracket Notation Reference (eleven_v3 only)

Brackets are stage directions — NOT spoken. The model uses them to modulate delivery.

**Placement:**
- Start of line: `[whispered, fearful] I don't think we should be here.`
- Mid-line: `I'm fine. [suddenly cold] Don't touch me.`
- Multiple: `[tentative] Maybe we could... [gaining confidence] No. We definitely should.`

**Quality guide:**
- Weak: `[sad]`
- Strong: `[grief mixed with fragile hope, she's reading a dead person's letter, voice should tremble slightly]`

**Built-in tags:** `[happy]`, `[sad]`, `[excited]`, `[angry]`, `[whispers]`, `[sighs]`, `[laughs]`, `[crying]`, `[sarcastic]`, `[curious]`

**Additional delivery controls:**
- CAPS for emphasis: "I NEED you to listen"
- Ellipsis for pauses: "I thought... maybe not"
- Dashes for interruptions: "Wait, I didn't—"

### 4d. Request Stitching

- `previous_request_ids`: up to 3 `request-id` values from prior TTS calls for prosody continuity
- IDs come from the `request-id` response header
- IDs expire ~2 hours after generation
- **`previous_text` is IGNORED if `previous_request_ids` is provided** — mutually exclusive

---

## 5. TEXT-TO-DIALOGUE — ElevenLabs (Multi-Voice)

### 5a. Scene-Batched Dialogue with Timestamps

Primary endpoint for Phase 4 bulk TTS.

```
POST https://api.elevenlabs.io/v1/text-to-dialogue/with-timestamps?output_format=mp3_44100_128
xi-api-key: {ELEVENLABS_API_KEY}
Content-Type: application/json
```

**Request Body:**
```json
{
  "inputs": [
    {"text": "[reading aloud from a letter] If you're reading this...", "voice_id": "pNInz6obpgDQGcFmaJgB"},
    {"text": "[quiet disbelief] Ready for what?", "voice_id": "pNInz6obpgDQGcFmaJgB"},
    {"text": "[warm, steady] It means you're not running anymore.", "voice_id": "aBcDeFgHiJkLmNoPqRsT"}
  ],
  "model_id": "eleven_v3",
  "settings": {"stability": 0.5},
  "language_code": "en",
  "seed": null
}
```

**Limits:**
- Max **10 unique voice_ids** per request
- No documented limit on `inputs` array length
- `settings.stability` is GLOBAL (no per-line override)
- Only `eleven_v3` model supported

**Response:**
```json
{
  "audio_base64": "{base64 combined audio}",
  "alignment": { "characters": [...], "character_start_times_seconds": [...], "character_end_times_seconds": [...] },
  "normalized_alignment": { "..." },
  "voice_segments": [
    {
      "voice_id": "pNInz6obpgDQGcFmaJgB",
      "start_time": 0.0,
      "end_time": 3.2,
      "character_start_index": 0,
      "character_end_index": 52,
      "dialogue_input_index": 0
    }
  ]
}
```

`dialogue_input_index` maps each segment back to the `inputs` array. Use `start_time`/`end_time` with ffmpeg to slice per-line audio.

---

## 6. MODEL ID REFERENCE

### ElevenLabs

| Model ID | Use For | Bracket Support |
|----------|---------|-----------------|
| `eleven_v3` | TTS + Dialogue (must set explicitly for TTS) | YES |
| `eleven_multilingual_v2` | TTS default — DO NOT USE for ScreenWire | NO |
| `eleven_flash_v2_5` | Low-latency TTS (not used in ScreenWire) | NO |
| `eleven_ttv_v3` | Voice Design + Remix | N/A |

### Replicate

| Model ID | Use For | Pricing |
|----------|---------|---------|
| `black-forest-labs/flux-2-pro` | Text-to-image, image-to-image | ~$0.055/image |
| `prunaai/p-video` | Dialogue video (lip-sync from audio) | $0.02/sec @720p |
| `xai/grok-imagine-video` | Non-dialogue video (native audio gen) | $0.05/sec @720p |

---

## 7. RATE LIMITS

### ElevenLabs (concurrent requests by plan)

| Plan | Concurrent | Burst (3x, 2x cost) |
|------|-----------|---------------------|
| Free | 2 | 6 |
| Starter | 3 | 9 |
| Creator | 5 | 15 |
| Pro | 10 | 30 |
| Scale | 15 | 45 |

HTTP 429 on exceed. Failed requests do NOT consume character credits.

### Replicate

- Predictions: 600/minute
- Other endpoints: 3,000/minute
- ScreenWire Video Agent: self-limits to 3 concurrent predictions

---

## 8. OUTPUT FORMAT REFERENCE

### ElevenLabs Audio

Recommended: `mp3_44100_128`

Available: `mp3_44100_128`, `mp3_44100_192` (Creator+), `mp3_44100_96`, `mp3_44100_64`, `mp3_44100_32`, `mp3_24000_48`, `mp3_22050_32`, `wav_44100`, `wav_48000` (Pro+), `pcm_44100`, `opus_48000_128`

### Replicate Images

Recommended: `png` for composed frames, `webp` for previews/thumbnails

---

## 9. COST ESTIMATION (3-scene "short" project)

| Category | Count | Unit Cost | Total |
|----------|-------|-----------|-------|
| Mood boards | 2-3 | $0.055 | $0.17 |
| Cast composites | 2-3 | $0.055 | $0.17 |
| Location images | 2-3 | $0.055 | $0.17 |
| Prop images | 2-4 | $0.055 | $0.22 |
| Composed frames | 9-15 | $0.055 | $0.83 |
| Voice design | 2-3 chars | ~free (chars only) | $0.10 |
| Scene dialogue TTS | 3 scenes | ~$0.30/scene | $0.90 |
| p-video clips | 5-8 | ~$0.10-0.20/clip | $1.20 |
| grok-video clips | 4-7 | ~$0.15-0.25/clip | $1.40 |
| **Total estimated** | | | **~$5-8** |

---

*This document governs. If any agent prompt, skill script, or gateway implementation conflicts with this document, this document wins.*
