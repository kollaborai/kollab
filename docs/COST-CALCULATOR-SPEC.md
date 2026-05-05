Session Cost Calculator + Widget Integration v3
=================================================

context:
  a user hit their openai plus plan limit burning 112M tokens with zero
  cache hits on gpt-5.4 (fixed store_responses default). he wants
  per-turn and per-session cost visibility in the status bar so users
  can see what they're spending in real time.

  providers don't return cost in API responses. we calculate from
  token counts + model pricing. openrouter already fetches pricing
  data (openrouter_model_info.py lines 183-188). for openai and
  anthropic, we need a config-based lookup.

  v3 fixes from second agent review:
  - CRITICAL: anthropic prompt_tokens ALREADY includes cache_read_tokens
    (transformers.py line 606: total_input = input + cache_creation + cache_read)
    v2 formula was WRONG - it double-counted cache, overcharging 11x
  - gpt-5.4 completion price is $15/1M not $10/1M
  - gpt-5.4 cache_discount is 0.1 not 0.5 (openai responses api)
  - openrouter returns input_cache_read field not currently captured
  - model matching must handle dot-separated names (glm-5.1)
  - azure_openai uses openai-style token accounting
  - custom provider uses openai-style token accounting
  - openrouter sentinel values: "0" and "-1" mean no pricing


architecture: pricing registry pattern
---------------------------------------

  PricingRegistry (singleton):
    - dict of {provider_type: {model_id: ModelPricing}}
    - ModelPricing(prompt_per_token: float, completion_per_token: float, cache_discount: float)
    - register_provider_pricing(provider_type, model_id, pricing)
    - get_pricing(provider_type, model_id) -> Optional[ModelPricing]
    - model matching: exact match, then segment-based prefix match

  who feeds it:
    - openrouter_provider: on _fetch_metadata(), registers all models
      with pricing converted from per-token strings to floats
      (filters out sentinel values "0" and "-1")
    - pricing.json: loaded at startup, registers static pricing for
      openai/anthropic/custom providers
    - custom/glm providers: no pricing data -> $0.00 (acceptable)

  who reads it:
    - cost_calculator: called from queue_processor, takes provider+model
    - never touches provider internals directly


token accounting (CRITICAL - get this wrong = wrong costs)
----------------------------------------------------------

  openai / openai_responses / azure_openai / custom / openrouter:
    prompt_tokens INCLUDES cache_read_tokens (subset)
    so: unique_prompt = prompt_tokens - cache_read_tokens

  anthropic:
    prompt_tokens = input_tokens + cache_creation + cache_read
    prompt_tokens ALREADY includes cache_read_tokens
    so: unique_prompt = prompt_tokens  (do NOT subtract cache_read)
    cache_read gets its OWN discounted line

  this difference is why we can't use one formula for all providers.


files to create:

  1. packages/kollabor-ai/src/kollabor_ai/pricing_registry.py
     - singleton PricingRegistry
     - ModelPricing dataclass: prompt_per_token, completion_per_token, cache_discount
     - register_provider_pricing(provider_type, model_id, ModelPricing)
     - get_pricing(provider_type, model_id) -> Optional[ModelPricing]
       - exact match first
       - for openrouter: try stripping namespace prefix (openai/gpt-4o -> gpt-4o)
       - segment-based prefix match: split model id by '-', compare segments
         gpt-4o matches gpt-4o-2024-05-13 (gpt-4o is prefix of gpt-4o-2024-05-13 segments)
         gpt-4 does NOT match gpt-4o (different segments: [gpt,4] vs [gpt,4o])
         glm-5 matches glm-5.1 (dot stays within segment, [glm,5] prefix of [glm,5.1])
     - load_from_file(path): reads pricing.json, registers all entries

  2. packages/kollabor-ai/src/kollabor_ai/cost_calculator.py
     - calculate_cost(provider_type, model, prompt_tokens, completion_tokens, cache_read_tokens) -> float
     - looks up pricing from PricingRegistry.get_pricing()
     - returns 0.0 if no pricing found (no crash, no warning spam)
     - formula (per-token prices, no division needed):
       if provider in (openai, openai_responses, azure_openai, custom, openrouter):
         unique_prompt = prompt_tokens - cache_read_tokens
         cost = unique_prompt * prompt_price
              + completion_tokens * completion_price
              + cache_read_tokens * prompt_price * cache_discount
       if provider == anthropic:
         cost = prompt_tokens * prompt_price
              + completion_tokens * completion_price
              + cache_read_tokens * prompt_price * cache_discount
       else:
         cost = 0.0
     - all prices are per-token floats (not per-M), so no division needed

  3. packages/kollabor-ai/src/kollabor_ai/default_pricing.json (bundled)
     - static pricing for openai/anthropic models in USD per-token
     - NOTE: default_pricing.json uses per-MILLION rates (gpt-4o prompt = 2.50)
       loader converts to per-token for ModelPricing. cache_read_per_million is
       optional; omitting it defaults cache_discount to 0.1 (10% of prompt rate).
       because that's what openrouter returns and it simplifies math
     - users override via ~/.kollab/pricing.json


files to modify:

  4. packages/kollabor-ai/src/kollabor_ai/providers/openrouter_model_info.py
     - add public method get_pricing(model_id) -> Optional[ModelPricing]
     - after _fetch_metadata() succeeds, register all pricing
       into the PricingRegistry singleton
     - also capture input_cache_read from openrouter response
     - convert string prices to floats, filter sentinel values:
       skip when prompt is "0" or "-1" (means no pricing available)

  5. packages/kollabor-ai/src/kollabor_ai/api_communication_service.py
     - add public property: provider_type -> str
       returns self._profile.provider if available, else ""
     - queue_processor can then access self.api_service.provider_type

  6. kollabor/state/snapshots.py (SessionStats dataclass, line 85)
     - add fields: cost_usd: float = 0.0, total_cost_usd: float = 0.0
     - override from_dict to filter unknown keys:
       known = {f.name for f in dataclasses.fields(cls)}
       return cls(**{k: v for k, v in data.items() if k in known})
     - THIS IS CRITICAL: without it, adding cost fields breaks older
       attach sessions that don't have these keys in their state

  7. kollabor/state/local.py (line 315-326)
     - add cost_usd and total_cost_usd extraction from raw dict:
       cost_usd=float(stats.get("cost_usd", 0.0)),
       total_cost_usd=float(stats.get("total_cost_usd", 0.0)),

  8. packages/kollabor-agent/src/kollabor_agent/queue_processor.py (line 623)
     - after token_usage update:
       provider_type = getattr(self.api_service, "provider_type", "")
       model = getattr(self.api_service, "model", "unknown")
       turn_cost = calculate_cost(provider_type, model,
                                   prompt_tokens, completion_tokens,
                                   cache_read_tokens)
       self.session_stats["cost_usd"] = turn_cost
       self.session_stats["total_cost_usd"] = (
         self.session_stats.get("total_cost_usd", 0.0) + turn_cost)
     - session_stats is a plain dict shared by reference, this is safe

  9. kollabor/state/refresher.py (line 165)
     - add to _gather_flat_state:
       flat["cost_usd"] = getattr(stats, "cost_usd", 0.0)
       flat["total_cost_usd"] = getattr(stats, "total_cost_usd", 0.0)

  10. packages/kollabor-tui/src/kollabor_tui/status/core_widgets.py (line 330)
      - read cost from both paths:
        local: ctx.llm_service.session_stats.get("total_cost_usd", 0.0)
        attach: ctx.remote_state.get("total_cost_usd", 0.0)
      - display formats (dynamic width calculation, same pattern as existing):
        extended: "5 msg | 12.3K tok | $0.42 | cache 8.2K"
        full:     "5 msg | 12.3K tok | $0.42"
        compact:  "5m|12Kt|$0"
        minimal:  "$0.42"
      - width thresholds are DYNAMIC (calculate len of each format, compare to width)
      - cost formatting: < $0.01 -> "$0.00", < $1 -> "$0.XX", < $100 -> "$X.XX", >= $100 -> "$XXX"
      - color: T().warning[0] when > $1, T().text_dim otherwise


default_pricing.json (per-million, USD — loader converts to per-token):

  {
    "openai": {
      "gpt-4o":       { "prompt_per_million":  2.50, "completion_per_million": 10.00, "cache_read_per_million": 1.25 },
      "gpt-4o-mini":  { "prompt_per_million":  0.15, "completion_per_million":  0.60, "cache_read_per_million": 0.075 },
      "gpt-5.4":      { "prompt_per_million":  2.50, "completion_per_million": 15.00, "cache_read_per_million": 0.25 },
      "gpt-4-turbo":  { "prompt_per_million": 10.00, "completion_per_million": 30.00, "cache_read_per_million": 5.00 },
      "o1":           { "prompt_per_million": 15.00, "completion_per_million": 60.00, "cache_read_per_million": 7.50 },
      "o3-mini":      { "prompt_per_million":  1.10, "completion_per_million":  4.40, "cache_read_per_million": 0.55 }
    },
    "openai_responses": {
      "gpt-5.4":      { "prompt_per_million":  2.50, "completion_per_million": 15.00, "cache_read_per_million": 0.25 }
    },
    "azure_openai": {
      "gpt-4o":       { "prompt_per_million":  2.50, "completion_per_million": 10.00, "cache_read_per_million": 1.25 }
    },
    "anthropic": {
      "claude-sonnet-4-6": { "prompt_per_million":  3.00, "completion_per_million": 15.00, "cache_read_per_million": 0.30 },
      "claude-opus-4-7":   { "prompt_per_million": 15.00, "completion_per_million": 75.00, "cache_read_per_million": 1.50 },
      "claude-haiku-4-5":  { "prompt_per_million":  0.80, "completion_per_million":  4.00, "cache_read_per_million": 0.08 }
    },
    "custom": {
      "GLM-5.1": { "prompt_per_million": 0.60, "completion_per_million": 2.20, "cache_read_per_million": 0.11 }
    },
    "gemini": {}
  }


model matching rules (segment-based):

  1. exact match on full model id
  2. for openrouter: strip namespace prefix, then try exact match
     e.g. "openai/gpt-4o" -> try "gpt-4o"
  3. segment-based prefix match:
     split both ids by '-' character
     compare segment by segment
     "gpt-4o" -> ["gpt","4o"] matches "gpt-4o-2024-05-13" -> ["gpt","4o","2024","05","13"]
     "gpt-4" -> ["gpt","4"] does NOT match "gpt-4o" -> ["gpt","4o"]
     "glm-5" -> ["glm","5"] matches "glm-5.1" -> ["glm","5.1"]
       (dots stay within segment, string prefix match on "5" vs "5.1" works)
  4. no match -> return None -> cost = 0.0


execution order:

  1. create pricing_registry.py (singleton + segment-based model matching)
  2. create default_pricing.json (bundled static pricing)
  3. create cost_calculator.py (calculation using registry, correct per-provider formulas)
  4. modify openrouter_model_info.py (register pricing into registry, capture cache pricing)
  5. modify api_communication_service.py (expose provider_type)
  6. modify snapshots.py (add cost fields + from_dict backward compat)
  7. modify local.py (map cost keys to DTO)
  8. modify queue_processor.py (calculate cost per turn)
  9. modify refresher.py (forward cost to flat dict)
  10. modify core_widgets.py (display cost with dynamic widths)


verification:

  - unit test: model matching (exact, segment prefix, openrouter namespace, dot segments)
  - unit test: cost calculation with known inputs/expected outputs
    - specifically test anthropic vs openai formula difference
    - test anthropic: prompt=1000, completion=100, cache_read=500, sonnet pricing
      expected: 1000*0.000003 + 100*0.000015 + 500*0.000003*0.1 = 0.003 + 0.0015 + 0.00015 = 0.00465
    - test openai: prompt=1000, completion=100, cache_read=500, gpt-4o pricing
      expected: (1000-500)*0.0000025 + 100*0.00001 + 500*0.0000025*0.5 = 0.00125 + 0.001 + 0.000625 = 0.002875
  - unit test: from_dict with unknown keys doesn't crash
  - integration: run kollabor, check status bar shows cost
  - integration: check attach mode shows cost (not $0.00)
  - integration: switch providers mid-session, cost keeps accumulating
  - integration: unknown model shows $0.00 (not crash)
  - integration: openrouter uses dynamic pricing from cache
  - integration: openrouter cache empty shows $0.00 without error
