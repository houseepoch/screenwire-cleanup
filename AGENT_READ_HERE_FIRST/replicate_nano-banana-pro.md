# Replicate: Nano Banana Pro (Google)

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
