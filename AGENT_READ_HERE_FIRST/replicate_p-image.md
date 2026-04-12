# Replicate: P-Image (PrunaAI)

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
