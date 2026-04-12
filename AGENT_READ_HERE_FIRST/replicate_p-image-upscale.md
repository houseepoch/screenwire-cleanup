# Replicate: P-Image-Upscale (PrunaAI)

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
