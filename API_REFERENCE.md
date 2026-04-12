# ScreenWire AI - API Reference

> Generated file. Do not hand-edit this document.
> Rebuild with `python3 build_api_reference.py`. Source fragments are loaded only from `AGENT_READ_HERE_FIRST/`.

## Source Fragments
- `AGENT_READ_HERE_FIRST/API_TOOL_REFERENCE.md`
- `AGENT_READ_HERE_FIRST/replicate_grok-imagine-video.md`
- `AGENT_READ_HERE_FIRST/replicate_nano-banana-2.md`
- `AGENT_READ_HERE_FIRST/replicate_nano-banana-pro.md`
- `AGENT_READ_HERE_FIRST/replicate_p-image-upscale.md`
- `AGENT_READ_HERE_FIRST/replicate_p-image.md`

---

## API & Tool Reference

_Source: `AGENT_READ_HERE_FIRST/API_TOOL_REFERENCE.md`_

> **Priority 0 — Agents read this FIRST.**
>
> Source fragment for the generated repo-root `API_REFERENCE.md`.
>
> Keep API research notes under `AGENT_READ_HERE_FIRST/` only. The aggregate
> reference is built from `API_*.md`, `api_*.md`, and `replicate_*.md` files
> in this directory via `python3 build_api_reference.py`.
>
> Put shared API contract notes here. Put provider-specific model research in
> sibling source fragments such as `replicate_*.md`.

---

## Replicate: Grok Imagine Video (xAI)

_Source: `AGENT_READ_HERE_FIRST/replicate_grok-imagine-video.md`_

> **Purpose**: Image-to-video model that animates still images into short videos with synchronized audio.

## Links

- **Replicate Model**: https://replicate.com/xai/grok-imagine-video

## Specs

| Property | Value |
|----------|-------|
| Type | Image-to-video / Text-to-video / Video editing |
| Architecture | Autoregressive mixture-of-experts (Aurora) |
| Video Duration | 1–15 seconds |
| Resolutions | 480p, 720p |
| Aspect Ratios | 16:9, 9:16, 1:1, 4:3, 3:4, 3:2, 2:3 |
| Audio | Native generation, auto-synchronized |
| Video Edit Input Limit | 8.7 seconds |

## Pricing

Pricing is usage-based on Replicate. Exact per-run cost not published on the model page — check Replicate billing for current rates. Video models are typically priced per second of output.

## Generation Modes

| Mode | Description |
|------|-------------|
| **Normal** | Balanced, professional results |
| **Fun** | Dynamic, playful interpretations |
| **Custom** | Precise control over generation |

## Input Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `prompt` | string | *required* | Text description of desired video content |
| `image` | URI | — | Input image to animate (image-to-video mode) |
| `video` | URI | — | Input video to edit (video editing mode, max 8.7s) |
| `mode` | string | `"normal"` | `normal`, `fun`, or `custom` |
| `duration` | integer | — | Video length in seconds (1–15) |
| `resolution` | string | `"720p"` | `480p` or `720p` |
| `aspect_ratio` | string | `"16:9"` | 16:9, 9:16, 1:1, 4:3, 3:4, 3:2, 2:3 |

*Note: Full parameter schema not published on Replicate — above is inferred from documentation. Check the API tab for definitive schema.*

## Output

Returns a video file URL (MP4) with synchronized audio.

## Model Notes

- **Three modes**: text-to-video, image-to-video, and video editing
- Native audio generation — background music, sound effects, and ambient audio are generated and synced automatically
- Supports character animation with potential lip-sync capabilities
- Prompt structure recommendation: "Subject + Action + Setting + Camera + Lighting/Mood" in natural sentences
- Good for: product showcases, portrait animation, creative content, social media clips
- Video editing mode accepts input videos up to 8.7 seconds
- Uses xAI's Aurora autoregressive mixture-of-experts architecture

---

## Replicate: Nano Banana 2 (Google)

_Source: `AGENT_READ_HERE_FIRST/replicate_nano-banana-2.md`_

> **Purpose**: Fast, high-quality image generation with multi-image reference support, conversational editing, and 14 aspect ratios. Powered by Gemini 3.1 Flash Image.

## Links

- **Replicate Model**: https://replicate.com/google/nano-banana-2
- **Google API Docs**: https://ai.google.dev/gemini-api/docs/models/gemini-3.1-flash-image-preview

## Specs

| Property | Value |
|----------|-------|
| Type | Text-to-image / Image editing |
| Underlying Model | Gemini 3.1 Flash Image |
| Output Formats | JPG (default), PNG |
| Resolutions | 512px, 1K, 2K, 4K |
| Max Reference Images | 14 |
| Aspect Ratios | 1:1, 2:3, 3:2, 3:4, 4:3, 4:5, 5:4, 9:16, 16:9, 21:9, 1:4, 4:1, 1:8, 8:1 |

## Pricing

Pricing is usage-based on Replicate. Exact per-image cost not published on the model page — check Replicate billing for current rates. Expected to be comparable to nano-banana-pro (see that doc for reference pricing).

## Input Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `prompt` | string | *required* | Text description of the desired image |
| `image_input` | array[URI] | `[]` | Up to 14 reference images for style transfer, editing, or multi-image fusion |
| `aspect_ratio` | string | `"1:1"` | One of 14 presets or `match_input_image` |
| `resolution` | string | `"2K"` | `512`, `1K`, `2K`, or `4K` |
| `output_format` | string | `"jpg"` | `jpg` or `png` |
| `match_input_image` | boolean | `false` | Auto-match aspect ratio to input image |

## Output

Returns a single image URL.

## Model Notes

- **Flash variant** — optimized for speed over the Pro model
- Strong text rendering with multilingual support
- Conversational editing: send an image + text prompt to modify it
- Multi-image fusion: blend up to 14 reference images in one composition
- Ultra-wide aspect ratios (1:8, 8:1) unique to this model
- Higher fidelity and stronger instruction following vs original Nano Banana
- Good for: rapid prototyping, product mockups, illustrations with text, batch generation

---

## Replicate: Nano Banana Pro (Google)

_Source: `AGENT_READ_HERE_FIRST/replicate_nano-banana-pro.md`_

> **Purpose**: Google's state-of-the-art image generation and editing model. Powered by Gemini 3 Pro Image.

## Links

- **Replicate Model**: https://replicate.com/google/nano-banana-pro

## Specs

| Property | Value |
|----------|-------|
| Type | Text-to-image / Image editing |
| Underlying Model | Gemini 3 Pro Image |
| Output Formats | JPG (default), PNG |
| Resolutions | 1K, 2K, 4K |
| Max Reference Images | 14 |
| Max People Consistency | 5 people |
| Aspect Ratios | 1:1, 2:3, 3:2, 3:4, 4:3, 4:5, 5:4, 9:16, 16:9, 21:9 |
| Watermark | SynthID (Google's digital watermark) |
| Total Runs | 20.7M+ |

## Pricing

| Resolution | Cost Per Image | Volume per $10 |
|------------|----------------|----------------|
| 1K | **$0.15** | ~66 images |
| 2K | **$0.15** | ~66 images |
| 4K | **$0.30** | ~33 images |
| Fallback model | **$0.035** | ~285 images |

## Input Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `prompt` | string | *required* | Text description of the desired image |
| `image_input` | array[URI] | `[]` | Up to 14 reference images for editing, style transfer, or multi-image fusion |
| `aspect_ratio` | enum | `match_input_image` | 1:1, 2:3, 3:2, 3:4, 4:3, 4:5, 5:4, 9:16, 16:9, 21:9 |
| `resolution` | enum | `"2K"` | `1K`, `2K`, or `4K` |
| `output_format` | enum | `"jpg"` | `jpg` or `png` |
| `safety_filter_level` | enum | `"block_only_high"` | `block_low_and_above` (strictest), `block_medium_and_above`, `block_only_high` (most permissive) |
| `allow_fallback_model` | boolean | `false` | Routes to bytedance/seedream-5 if at capacity |

## Output

Returns a single image URL (string, URI format).

## Model Notes

- **Pro variant** — higher quality than Nano Banana 2 (Flash) but slower and more expensive
- Generates accurate, legible text in multiple languages
- Uses Gemini 3 Pro's reasoning for context-rich, detailed visuals
- Can blend up to 14 images while maintaining consistency
- Maintains resemblance of up to 5 people across compositions
- Can access Google Search for real-time information integration
- Professional editing: camera angles, lighting, color grading, depth of field
- SynthID watermarking on all outputs
- `allow_fallback_model` routes to bytedance/seedream-5 when at capacity ($0.035/image)(do not default on please)
- Known limitations: occasional inaccuracies, text quality varies by language, visual artifacts with advanced features, character consistency not perfect, capacity constraints possible

---

## Replicate: P-Image-Upscale (PrunaAI)

_Source: `AGENT_READ_HERE_FIRST/replicate_p-image-upscale.md`_

> **Purpose**: Fast AI image upscaler supporting outputs up to 8 megapixels.

## Links

- **Replicate Model**: https://replicate.com/prunaai/p-image-upscale
- **Pruna API Docs**: https://docs.api.pruna.ai/guides/models/p-image-upscale

## Specs

| Property | Value |
|----------|-------|
| Type | Image upscaling |
| Speed | Under 1 second for 4 MP output |
| Max Output | 8 megapixels |
| Output Formats | JPEG, PNG, WebP |

## Pricing

| Resolution Range | Cost Per Image |
|------------------|----------------|
| 1–4 MP | **$0.005** |
| 5–8 MP | **$0.01** |

## Input Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `image` | URI | *required* | Input image to upscale |
| `mode` | string | `"target"` | `"target"` (set megapixels) or `"factor"` (set multiplier) |
| `target_megapixels` | number | — | Desired output in MP (1–8), target mode only |
| `factor` | number | — | Scale multiplier (1–8x), factor mode; output capped at 8 MP |
| `enhance_realism` | boolean | `true` | Improves realism, especially for AI-generated images |
| `enhance_details` | boolean | `false` | Sharpens textures and fine details |
| `output_format` | string | `"jpeg"` | `"jpeg"`, `"png"`, or `"webp"` |
| `quality` | integer | `80` | JPEG/WebP quality 0–100 |

## Output

Returns a single upscaled image URL in the specified format.

## Model Notes

- Two operational modes: target (set desired MP) vs factor (set multiplier)
- `enhance_realism` is on by default — particularly effective on AI-generated images
- `enhance_details` adds sharpening for textures and contrast
- Extremely cost-effective for pipeline upscaling workflows
- Ideal pairing: generate with p-image at low res → upscale with p-image-upscale to 4K+

---

## Replicate: P-Image (PrunaAI)

_Source: `AGENT_READ_HERE_FIRST/replicate_p-image.md`_

> **Purpose**: Sub-1-second text-to-image generation built for production use cases.

## Links

- **Replicate Model**: https://replicate.com/prunaai/p-image
- **Pruna Docs**: https://docs.pruna.ai/en/docs-add-performance-pages/docs%5Fpruna%5Fendpoints/performance%5Fmodels/p-image.html
- **Prompting Guide**: https://docs.pruna.ai/en/docs-add-performance-pages/docs%5Fpruna%5Fendpoints/image%5Fgeneration/advanced.html

## Specs

| Property | Value |
|----------|-------|
| Type | Text-to-image |
| Speed | Sub-1 second per image |
| Output Format | JPG |
| Resolution | 256–1440px per dimension (multiples of 16) |
| Preset Aspect Ratios | 1:1, 16:9, 9:16, 4:3, 3:4, 3:2, 2:3, custom |

## Pricing

| Metric | Cost |
|--------|------|
| Per image | **$0.005** |

## Input Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `prompt` | string | *required* | Text description for image generation |
| `aspect_ratio` | string | `"16:9"` | Preset ratio or `"custom"` for explicit w/h |
| `width` | integer | — | 256–1440px, multiples of 16 (custom mode only) |
| `height` | integer | — | 256–1440px, multiples of 16 (custom mode only) |
| `seed` | integer | random | Reproducible generation seed |
| `disable_safety_checker` | boolean | `false` | Toggle safety filtering |

## Output

Returns a single JPG image URL.

## Model Notes

- Strongest selling points: speed, prompt adherence, and text rendering quality
- Built for production workloads — high throughput, low cost
- Pruna also offers a direct API at `https://api.pruna.ai/v1/predictions` (separate from Replicate)
- Supports async (polling) and sync (`Try-Sync: true` header) generation modes via Pruna's own API
- Good for: retail product shots, gaming assets, advertising creatives, concept art
