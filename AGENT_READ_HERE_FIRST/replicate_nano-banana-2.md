# Replicate: Nano Banana 2 (Google)

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
