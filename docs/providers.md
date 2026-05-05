---
title: "Providers"
created: 2026-02-24
modified: 2026-04-09
status: active
---
# Providers

Kollab supports multiple LLM providers through a unified interface. Each provider has native or OpenAI-compatible implementations.

## Anthropic (Claude)

Native support for all Claude models.

### Environment Variable

```bash
export ANTHROPIC_API_KEY="<your-anthropic-api-key>"
```

### Configuration

```json
{
  "provider": "anthropic",
  "model": "claude-opus-4-6",
  "api_key": "<your-anthropic-api-key>",
  "temperature": 0.7,
  "max_tokens": 8192,
  "timeout": 120
}
```

### Supported Models

Any Claude model ID works. Examples:
- `claude-opus-4-6` - Most capable (recommended)
- `claude-sonnet-4-6` - Fast, balanced
- `claude-haiku-4-5-20251001` - Lightweight, efficient

### Features

- Native httpx client implementation
- Extended thinking block preservation
- Server-Sent Events (SSE) streaming
- Tool calling with incremental JSON

### API Endpoint

Default: `https://api.anthropic.com`

Supports custom `base_url` for proxies or enterprise endpoints.

## OpenAI (API Key)

Standard OpenAI API support using the official SDK.

### Environment Variable

```bash
export OPENAI_API_KEY="<your-api-key>"
```

### Configuration

```json
{
  "provider": "openai",
  "model": "gpt-5.4",
  "api_key": "<your-api-key>",
  "base_url": "https://api.openai.com/v1",
  "organization": "org-...",
  "temperature": 0.7,
  "max_tokens": 4096
}
```

### Supported Models

Any OpenAI model ID works. Examples:
- `gpt-5.4` - Most capable (recommended)
- `gpt-5.4-codex` - Optimized for code
- `o3` - Reasoning model

### Features

- Official `openai` AsyncOpenAI client
- Streaming with tool call accumulation
- Organization ID support
- Custom base URL for proxies

## OpenAI (OAuth / ChatGPT)

Use your ChatGPT Plus/Pro subscription directly. No API key required.

### Login

```bash
kollab --login openai
```

### Prerequisites

In ChatGPT web:
1. Go to Settings > Security (or Data Controls)
2. Enable "Device code authorization" (sometimes labeled "Codex")

### How It Works

1. CLI displays a verification code
2. Browser opens to `auth.openai.com/codex/device`
3. Enter the code to authorize
4. Tokens stored at `~/.kollab/oauth/openai_tokens.json`

### Configuration

The `openai_responses` provider is auto-configured after login. No manual configuration needed.

### Token Management

- Tokens expire in ~8 days
- Auto-refresh on expiry
- Manual re-auth required if refresh fails

### API Endpoint

ChatGPT backend: `https://chatgpt.com/backend-api/codex`

This is different from `api.openai.com` - it accepts OIDC session tokens directly.

## Google Gemini

Native support for Google's Gemini models.

### Environment Variable

```bash
export GEMINI_API_KEY=...
```

### Configuration

```json
{
  "provider": "gemini",
  "model": "gemini-3.1-pro-preview",
  "api_key": "...",
  "temperature": 0.7,
  "max_tokens": 8192
}
```

### Supported Models

Any Gemini model ID works. Examples:
- `gemini-3.1-pro-preview` - Latest Pro (recommended)
- `gemini-2.5-flash-lite` - Budget, high-throughput

### Features

- httpx async client
- API key via URL parameter (`?key=`) and `x-goog-api-key` header
- Streaming with SSE parsing
- Function calling with `functionCall` format

### API Endpoint

Default: `https://generativelanguage.googleapis.com`

Supports custom `base_url` for proxies.

## Azure OpenAI

Azure-hosted OpenAI models with enterprise features.

### Environment Variables

```bash
export AZURE_OPENAI_API_KEY=...
export AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com
```

### Configuration

```json
{
  "provider": "azure_openai",
  "model": "gpt-5.4",
  "api_key": "...",
  "azure_endpoint": "https://your-resource.openai.azure.com",
  "api_version": "2024-02-15-preview",
  "deployment_id": "my-deployment"
}
```

### Fields

| Field | Required | Description |
|-------|----------|-------------|
| `azure_endpoint` | Yes | Your Azure OpenAI resource URL |
| `api_version` | No | API version (default: 2024-02-15-preview) |
| `deployment_id` | No | Deployment name (overrides model) |

### Features

- Extends OpenAI provider
- Custom base URL construction
- Deployment ID vs model name support

## OpenRouter

Unified gateway to 100+ models from multiple providers.

### Environment Variable

```bash
export OPENROUTER_API_KEY="<your-openrouter-api-key>"
```

### Configuration

```json
{
  "provider": "openrouter",
  "model": "anthropic/claude-opus-4-6",
  "api_key": "<your-openrouter-api-key>",
  "http_referer": "https://myapp.com",
  "x_title": "MyApp"
}
```

### Model Format

OpenRouter uses `provider/model` format:
- `openai/gpt-5.4`
- `anthropic/claude-opus-4-6`
- `google/gemini-2.0-flash-exp`

See [openrouter.ai/models](https://openrouter.ai/models) for the full list.

### Site Tracking

Optional headers for OpenRouter rankings:
```json
{
  "http_referer": "https://myapp.com",
  "x_title": "My Application"
}
```

### Features

- OpenAI SDK with custom base URL
- Automatic model routing and fallback
- Cost tracking via OpenRouter dashboard

## Custom / OpenAI-Compatible

Any OpenAI-compatible endpoint: Ollama, LM Studio, vLLM, or custom APIs.

### Environment Variables

```bash
# Profile-based
KOLLAB_OLLAMA_PROVIDER=custom
KOLLAB_OLLAMA_BASE_URL=http://localhost:11434/v1
KOLLAB_OLLAMA_MODEL=llama3.3
```

### Configuration

```json
{
  "provider": "custom",
  "base_url": "http://localhost:11434/v1",
  "model": "llama3.3",
  "api_key": "",  // Optional for local endpoints
  "temperature": 0.7,
  "max_tokens": 4096
}
```

### Common Local Endpoints

| Tool | Default URL |
|------|-------------|
| Ollama | `http://localhost:11434/v1` |
| LM Studio | `http://localhost:1234/v1` |
| vLLM | `http://localhost:8000/v1` |
| text-generation-webui | `http://localhost:5000/v1` |

### Features

- aiohttp client for async requests
- OpenAI-compatible request/response format
- Tool calling support (if endpoint supports it)
- API key optional for local endpoints

## Provider Comparison

| Provider | Auth | Streaming | Tools | OAuth |
|----------|------|-----------|-------|-------|
| Anthropic | API Key | Yes | Yes | No |
| OpenAI | API Key | Yes | Yes | No |
| OpenAI Responses | OAuth | Yes | Yes | Yes |
| Gemini | API Key | Yes | Yes | No |
| Azure | API Key | Yes | Yes | No |
| OpenRouter | API Key | Yes | Yes | No |
| Custom | Optional | Yes | Yes* | No |

*Depends on endpoint implementation.

## Choosing a Provider

- **Claude fans**: Use Anthropic for best quality
- **ChatGPT subscribers**: Use `--login openai` for OAuth
- **Multi-model**: OpenRouter provides unified access
- **Local/privacy**: Ollama or LM Studio with custom provider
- **Enterprise**: Azure OpenAI for compliance and data residency
- **Cost-conscious**: Gemini 2.0 Flash for speed/price ratio

## Troubleshooting

### API Key Not Found

Check env var is exported in your shell:
```bash
echo $ANTHROPIC_API_KEY
```

For profiles, verify field names match exactly:
```bash
# Correct
KOLLAB_MY_PROFILE_API_KEY=...

# Wrong (missing API_KEY)
KOLLAB_MY_PROFILE_KEY=...
```

### OAuth Token Expired

Re-run login:
```bash
kollab --login openai
```

Old tokens are automatically replaced.

### Custom Endpoint Connection Fails

Verify the endpoint is running:
```bash
curl http://localhost:11434/v1/models
```

Check `base_url` includes `/v1` if needed:
```json
{"base_url": "http://localhost:11434/v1"}
```

### Module Not Installed

Some providers require additional packages:

```bash
pip install openai   # OpenAI, Azure, OpenRouter
pip install httpx    # Anthropic, Gemini
pip install aiohttp  # Custom provider
```
