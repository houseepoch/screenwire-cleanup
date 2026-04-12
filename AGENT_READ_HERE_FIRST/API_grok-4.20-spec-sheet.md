# Grok 4.20 — Full Specification Sheet

**Provider:** xAI (SpaceX/xAI)
**Release Date:** March 31, 2026 (Beta: February 17, 2026)
**Model ID:** `grok-4.20-reasoning` / `grok-4.20-multi-agent-0309`
**Knowledge Cutoff:** September 1, 2025 (November 2024 for Grok 3/4 base)
**Status:** Generally Available via API, X Premium+, SuperGrok

---

## Model Architecture

| Attribute | Detail |
|---|---|
| **Architecture** | Proprietary multi-agent reasoning model |
| **Multi-Agent System** | 4 built-in specialized agents (Grok, Harper, Benjamin, Lucas) |
| **Agent Workflow** | Think → Debate → Verify → Synthesize consensus |
| **Parameters** | ~500B confirmed ("small" variant); larger variants in training |
| **Training Infrastructure** | Colossus supercluster (~200,000+ GPUs, scaling to 1M+) |
| **Training Data** | Public internet, third-party datasets, synthetic data, X firehose (~68M English tweets/day) |
| **Reasoning** | Native reasoning tokens (can be enabled/disabled via API parameter) |
| **Hallucination Rate** | ~4.2% (65% reduction from Grok 4's ~12%) |

---

## Context Window & Limits

| Attribute | Detail |
|---|---|
| **Context Window** | **2,000,000 tokens** |
| **Max Output Tokens** | 128,000 tokens |
| **Image Input** | Supported — jpg/jpeg, png; max 20 MiB per image; no limit on count |
| **Image Tokens** | 256–1,792 tokens per image (depends on resolution) |
| **Video Input** | Supported (via X Video Understanding tool) |
| **Text-to-First-Token Latency** | ~10–11 seconds (reasoning mode); 45–90s for uncached 2M token fills |
| **Output Speed** | ~187–199 tokens/second |

---

## Pricing (per 1M tokens, USD)

| Token Type | Standard | Batch API (50% off) |
|---|---|---|
| **Input** | $2.00 | $1.00 |
| **Cached Input** | Discounted (auto) | Discounted (auto) |
| **Output** | $6.00 | $3.00 |
| **Reasoning** | Billed as output ($6.00) | $3.00 |

### Tool Invocation Costs (per 1,000 calls)

| Tool | Cost | API Name |
|---|---|---|
| Web Search | $5.00 | `web_search` |
| X Search | $5.00 | `x_search` |
| Code Execution | $5.00 | `code_execution` / `code_interpreter` |
| File Attachments | $10.00 | `attachment_search` |
| Collections / RAG | $2.50 | `collections_search` / `file_search` |
| Image Understanding | Token-based | `view_image` |
| X Video Understanding | Token-based | `view_x_video` |
| Remote MCP | Token-based | Per MCP server |

### Other Pricing

| Service | Cost |
|---|---|
| Voice Agent API | $0.05/min ($3.00/hr) |
| Text-to-Speech | $4.20 / 1M characters |
| Usage violation fee | $0.05 per flagged request |

---

## Model Variants

| Model ID | Input/M | Output/M | Context | Notes |
|---|---|---|---|---|
| `grok-4.20-reasoning` | $2.00 | $6.00 | 2M | Flagship — reasoning enabled |
| `grok-4.20-multi-agent-0309` | $2.00 | $6.00 | 2M | 4-agent collaborative architecture |
| `grok-4` (0709) | $3.00 | $15.00 | 256K | Previous flagship, always-reasoning |
| `grok-4-fast-reasoning` | $0.20 | $0.50 | 2M | Budget reasoning model |
| `grok-4-fast-non-reasoning` | $0.20 | $0.50 | 2M | Budget, no chain-of-thought |
| `grok-code-fast-1` | $0.20 | $1.50 | 256K | Optimized for agentic coding |

---

## Capabilities

### Core

- **Reasoning:** Native chain-of-thought; enable/disable via `reasoning.enabled` parameter
- **Structured Outputs:** JSON mode with schema enforcement
- **Function Calling:** Full support for custom tool definitions
- **Streaming:** Server-sent events for real-time token delivery
- **Prompt Caching:** Automatic — no configuration needed; use `x-grok-conv-id` header to improve cache hit rate

### Built-in Server-Side Tools

- **Web Search** — live internet search and page browsing
- **X (Twitter) Search** — search posts, profiles, threads; real-time social data
- **Code Execution** — sandboxed Python environment
- **Collections Search (RAG)** — query uploaded document collections
- **File Attachments** — search through attached files
- **Image Understanding** — analyze images found via search tools
- **X Video Understanding** — analyze videos from X posts
- **Remote MCP Tools** — connect any Model Context Protocol server

### Multimodal

- **Input:** Text + Image (jpg/png, base64 or URL)
- **Output:** Text (+ image generation via separate `grok-4` image endpoint)
- **Image Generation:** Supported via dedicated endpoint ($0.02/image standard, $0.07/image high quality)
- **Video Generation:** Grok Imagine Video ($0.05/second)

---

## API Access

### Endpoints

| Protocol | URL |
|---|---|
| **REST API (Responses)** | `https://api.x.ai/v1/responses` |
| **REST API (Chat Completions)** | `https://api.x.ai/v1/chat/completions` |
| **gRPC** | See `xai-proto` public protobuf definitions |

### Regional Endpoints

| Region | Endpoint |
|---|---|
| US (us-east-1) | Default |
| EU (eu-west-1) | Available — same models and pricing |

### Authentication

- API key via `Authorization: Bearer $XAI_API_KEY`
- Keys created at `console.x.ai`
- Environment variable: `XAI_API_KEY`

---

## SDKs & Integration

### Official SDKs

| SDK | Install | Notes |
|---|---|---|
| **xAI Python SDK** | `pip install xai-sdk` | gRPC-based; sync (`Client`) + async (`AsyncClient`); Python 3.10+ |
| **OpenAI Python SDK** | `pip install openai` | Compatible — set `base_url="https://api.x.ai/v1"` |
| **Vercel AI SDK (JS)** | `npm install ai @ai-sdk/xai zod` | Official `@ai-sdk/xai` provider |
| **OpenAI Node SDK** | `npm install openai` | Compatible — set `baseURL` to `https://api.x.ai/v1` |

### API Compatibility

- **OpenAI Responses API** — fully supported (recommended)
- **OpenAI Chat Completions API** — supported
- **gRPC API** — high-performance alternative; public protobuf definitions at `github.com/xai-org/xai-proto`

### Code Editor Integrations

- Cursor, Windsurf, VS Code (via API key configuration)
- Codex CLI (set as custom provider)
- Claude Code (configurable provider)

### Third-Party Platforms

- OpenRouter
- Oracle Cloud Infrastructure (OCI) Generative AI
- LangChain / LlamaIndex (community integrations)

---

## Rate Limits

Rate limits are tiered based on spend. Check current limits at `console.x.ai/team/default/rate-limits`.

| Tier | Typical TPM | Typical RPM |
|---|---|---|
| **Default / Low** | 16K–200K tpm | 60–480 rpm |
| **Grok 4 Fast** | Up to 4M tpm | 480 rpm |
| **Grok 4.20** | Up to 10M tpm | 1,800 rpm |
| **Enterprise** | Custom | Custom |

- Exceeding limits returns HTTP `429`
- Batch API requests do not count against rate limits
- Request tier increases via xAI Console

---

## Advanced Features

### Batch API

- 50% off all token types (input, output, cached, reasoning)
- Asynchronous processing, typically within 24 hours
- No rate limit impact
- Text/language models only (image/video at standard rates)

### Prompt Caching

- Automatic — enabled for all requests, no configuration
- Prefix-matching: identical prompt prefixes served from cache
- Use `x-grok-conv-id` header with a constant UUID to increase cache hit rate
- Cached tokens billed at reduced rate

### Deferred Completions

- Queue requests for later processing
- Useful for non-urgent batch workloads

### Provisioned Throughput

- Reserved capacity for guaranteed performance
- Enterprise feature

### mTLS Authentication

- Mutual TLS for enhanced security
- Enterprise feature

---

## Benchmarks (Preliminary)

| Benchmark | Score | Notes |
|---|---|---|
| **Artificial Analysis Intelligence Index** | 49/100 | Well above median (31) for reasoning models in price tier |
| **LMArena ELO** | ~1505–1535 (est.) | Potentially #1 overall once fully ranked |
| **Alpha Arena (live trading)** | +12–34% returns | Only profitable AI model in competition |
| **ForecastBench** | #2 globally | Outperformed GPT-5, Gemini 3 Pro, Claude Opus 4.5 |
| **Hallucination Rate** | ~4.2% | Down from 12% (Grok 4); 65% reduction |
| **Output Speed** | 199 t/s | Well above median (69 t/s) for price tier |

---

## Limitations & Considerations

- **No fine-tuning** — limited preview only; not generally available
- **Stateless API** — no built-in conversation memory; developers must manage history
- **Long-context latency** — 2M token uncached requests incur 45–90s prefill time
- **Ecosystem maturity** — younger plugin/integration ecosystem vs. OpenAI/Anthropic
- **No `logprobs`** — `logprobs` field is ignored on Grok 4.20
- **No `presencePenalty` / `frequencyPenalty` / `stop`** — not supported on reasoning models
- **No `reasoning_effort`** — not available for Grok 4 models (errors if provided)
- **Image format** — only jpg/jpeg and png supported (no webp, gif, etc.)
- **Knowledge cutoff** — September 2025; requires Web Search / X Search tools for real-time data

---

## Quick Start

```python
# Using xAI native SDK
import os
from xai_sdk import Client
from xai_sdk.chat import user, system

client = Client(api_key=os.getenv("XAI_API_KEY"))
chat = client.chat.create(model="grok-4.20-reasoning")
chat.append(system("You are a helpful assistant."))
chat.append(user("Explain quantum computing in simple terms."))
response = chat.sample()
print(response.content)
```

```python
# Using OpenAI SDK (compatible)
from openai import OpenAI

client = OpenAI(
    api_key="YOUR_XAI_API_KEY",
    base_url="https://api.x.ai/v1",
)
response = client.responses.create(
    model="grok-4.20-reasoning",
    input=[
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Explain quantum computing in simple terms."},
    ],
)
print(response.output_text)
```

```javascript
// Using Vercel AI SDK
import { createXai } from '@ai-sdk/xai';
import { generateText } from 'ai';

const xai = createXai({ apiKey: process.env.XAI_API_KEY });
const { text } = await generateText({
  model: xai.responses('grok-4.20-reasoning'),
  system: "You are a helpful assistant.",
  prompt: "Explain quantum computing in simple terms.",
});
console.log(text);
```

---

## Resources

| Resource | URL |
|---|---|
| API Console | `https://console.x.ai` |
| Documentation | `https://docs.x.ai` |
| REST API Reference | `https://docs.x.ai/developers/rest-api-reference` |
| gRPC API Reference | `https://docs.x.ai/developers/grpc-api-reference` |
| Python SDK (GitHub) | `https://github.com/xai-org/xai-sdk-python` |
| Protobuf Definitions | `https://github.com/xai-org/xai-proto` |
| Cookbook & Examples | `https://github.com/xai-org/xai-cookbook` |
| System Prompts | `https://github.com/xai-org/grok-prompts` |
| Status Page | `https://status.x.ai` |

---

*Sources: xAI official documentation (docs.x.ai), Artificial Analysis, OpenRouter, independent reviews. Specifications current as of April 2026. Verify at docs.x.ai for the latest.*
