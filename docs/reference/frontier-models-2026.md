---
title: Current Frontier Models (April 2026)
updated: 2026-04-13
---

# Current Frontier Models

Reference for max output tokens, context windows, and pricing across
major LLM providers. Used to inform default max_tokens in kollab
provider configs.

Last verified: 2026-04-13


## OpenAI

model               context     max output  pricing (in/out per MTok)
-----------------   ---------   ----------  -------------------------
GPT-5.4             1,050,000   128,000     $2.50 / $15.00
GPT-5.4 Pro         1,050,000   128,000     $30.00 / $180.00
GPT-5.4 Mini          400,000   128,000     $0.75 / $4.50
GPT-5.4 Nano          400,000   128,000     $0.20 / $1.25

notes:
  - GPT-5.4 released 2026-03-05
  - native computer use, reasoning effort levels (none/low/med/high/xhigh)
  - prompts >272K input tokens: 2x input rate, 1.5x output rate
  - GPT-4.1 (legacy): 32,768 max output, 1,047,576 context


## Anthropic

model               context     max output  pricing (in/out per MTok)
-----------------   ---------   ----------  -------------------------
Claude Opus 4.6     1,000,000   128,000     $5.00 / $25.00
Claude Sonnet 4.6   1,000,000    64,000     $3.00 / $15.00
Claude Haiku 4.5      200,000    64,000     $1.00 / $5.00

notes:
  - batch API: Opus 4.6 + Sonnet 4.6 support 300K output tokens
    (requires output-300k-2026-03-24 beta header)
  - all current models support extended thinking + tool use
  - legacy: Opus 4.5 (64K out), Opus 4.1 (32K out), Opus 4.0 (32K out)


## Google

model               context     max output  pricing (in/out per MTok)
-----------------   ---------   ----------  -------------------------
Gemini 3.1 Pro      1,000,000    65,536     $2.00 / $12.00
Gemini 3.1 Flash-L  1,000,000    65,536     $0.25 / $1.50
Gemini 2.5 Pro      1,048,576    65,536     $1.25 / $10.00
Gemini 2.5 Flash    1,048,576    65,536     $0.15 / $0.60

notes:
  - Gemini 3.1 Pro released 2026-02-19 (current frontier)
  - 3.1 Pro: >200K input prompts: $4.00 / $18.00
  - 3.1 Flash-Lite: 2.5x faster TTFAT, 45% faster output vs 2.5 Flash
  - Gemini 3 Flash also in preview
  - maxOutputTokens param; silent truncation if not set explicitly
  - default maxOutputTokens is 8,192 -- must set explicitly for full 65K


## Z.AI (GLM)

model               context     max output  pricing (in/out per MTok)
-----------------   ---------   ----------  -------------------------
GLM-5.1               202,752    65,535     ~$1.00 / $4.00
GLM-5-Turbo           202,752   131,072     ~$0.50 / $2.00

notes:
  - 754B MoE architecture, 40B active params
  - GLM-5.1 is open-weight, SOTA on SWE-Bench Pro
  - extended thinking supported
  - coding plan API at api.z.ai/api/coding/paas/v4


## xAI (Grok)

model               context     max output  pricing (in/out per MTok)
-----------------   ---------   ----------  -------------------------
Grok 4.20           2,000,000   unlisted    $2.00 / $6.00
Grok 4.1 Fast       2,000,000   unlisted    (varies)

notes:
  - output limit not explicitly documented, bounded by context window
  - reasoning and non-reasoning variants available
  - released 2026-03-31


## Meta (Llama)

model               context     max output  pricing (in/out per MTok)
-----------------   ---------   ----------  -------------------------
Llama 4 Scout       10,000,000  unlisted    $0.15 / $0.50
Llama 4 Maverick       512,000  unlisted    $0.22 / $0.85

notes:
  - open-weight, available on multiple inference providers
  - Maverick on-demand: capped at 4K output; dedicated: uncapped
  - Behemoth not yet released (2T params, announced)
  - pricing varies by provider (above is typical hosted)


## DeepSeek

model               context     max output  pricing (in/out per MTok)
-----------------   ---------   ----------  -------------------------
DeepSeek V3.2       128,000      8,000      $0.30 / $0.50
DeepSeek V3.2 (R)   128,000     64,000      $0.30 / $0.50

notes:
  - V3.2 is current production model (api: deepseek-chat, deepseek-reasoner)
  - reasoner mode: 32K default, 64K max output
  - non-reasoning: 4K default, 8K max output
  - V4 announced but not publicly available as of 2026-04-13


## Mistral

model               context     max output  pricing (in/out per MTok)
-----------------   ---------   ----------  -------------------------
Mistral Large 3       262,000   unlisted    $2.00 / $6.00
Mistral Small 4       128,000   unlisted    $0.10 / $0.30

notes:
  - Large 3: 675B total params, 41B active (MoE)
  - released 2025-12-01


## Kollabor Default

ProviderConfig.max_tokens default: 128,000

This covers the highest common output across providers we support.
APIs that can't handle 128K will silently clamp to their max.
The auto-continuation system in queue_processor.py handles any
remaining truncation (stop_reason=length) by retrying up to 3 times.

For provider-specific tuning, set max_tokens explicitly in the
profile config under ~/.kollab/config.json:

  "profiles": {
    "my-profile": {
      "max_tokens": 65536,
      ...
    }
  }
