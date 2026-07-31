"""Sampling params must be omitted for models that reject them.

Claude 4.7+ / Fable 5 / Mythos 5 return a 400 when a request carries
temperature, top_p, or top_k. The registry marks those models with
``supports_sampling: false``; every provider that builds a payload has to
honor it, or the model is unusable.

Guards the unmarked case too: an unknown or older model must keep sending
temperature, otherwise a user's configured sampling silently stops applying.
"""

import unittest

from kollabor_ai.providers.anthropic_provider import AnthropicProvider
from kollabor_ai.providers.azure_provider import AzureOpenAIProvider
from kollabor_ai.providers.gemini_provider import GeminiProvider
from kollabor_ai.providers.models import (
    AnthropicConfig,
    AzureOpenAIConfig,
    GeminiConfig,
    OpenAIConfig,
)
from kollabor_ai.providers.openai_provider import OpenAIProvider

MESSAGES = [{"role": "user", "content": "hi"}]

# Registry-marked (400 on sampling params) vs everything else.
NO_SAMPLING = ("claude-opus-5", "claude-opus-4-7", "claude-sonnet-5", "claude-fable-5")
SAMPLING_OK = ("claude-opus-4-6", "claude-sonnet-4-6", "glm-5.2", "some-future-model")


class AnthropicSamplingTests(unittest.TestCase):
    def _request(self, model):
        provider = AnthropicProvider(
            AnthropicConfig(api_key="k", model=model, temperature=0.7, top_p=0.9)
        )
        return provider._prepare_request(list(MESSAGES))

    def test_omitted_for_reasoning_models(self):
        for model in NO_SAMPLING:
            request = self._request(model)
            self.assertNotIn("temperature", request, model)
            self.assertNotIn("top_p", request, model)
            # the rest of the payload is untouched
            self.assertEqual(request["model"], model)
            self.assertIn("max_tokens", request)

    def test_sent_for_everything_else(self):
        for model in SAMPLING_OK:
            request = self._request(model)
            self.assertEqual(request.get("temperature"), 0.7, model)
            self.assertEqual(request.get("top_p"), 0.9, model)


class OpenAICompatibleSamplingTests(unittest.TestCase):
    def test_openai_provider_honors_flag(self):
        omit = OpenAIProvider(
            OpenAIConfig(api_key="k", model="claude-opus-5", temperature=0.5, top_p=0.8)
        )._prepare_request_params(list(MESSAGES), None, False)
        self.assertNotIn("temperature", omit)
        self.assertNotIn("top_p", omit)

        keep = OpenAIProvider(
            OpenAIConfig(api_key="k", model="gpt-5.6", temperature=0.5, top_p=0.8)
        )._prepare_request_params(list(MESSAGES), None, False)
        self.assertEqual(keep["temperature"], 0.5)
        self.assertEqual(keep["top_p"], 0.8)

    def test_azure_matches_on_model_not_deployment(self):
        # The deployment id is an arbitrary user string; the flag has to be
        # resolved from the model name.
        params = AzureOpenAIProvider(
            AzureOpenAIConfig(
                api_key="a" * 32,  # azure config enforces a realistic key length
                model="claude-opus-5",
                deployment_id="my-deployment",
                azure_endpoint="https://example.openai.azure.com",
                temperature=0.5,
            )
        )._prepare_request_params(list(MESSAGES), None, False)
        self.assertEqual(params["model"], "my-deployment")
        self.assertNotIn("temperature", params)


class EveryPayloadBuilderIsGatedTests(unittest.TestCase):
    """No payload builder may send sampling params unconditionally.

    custom / openai_responses / openrouter were missed on the first pass: they
    each build their own payload and sent temperature (and top_p) regardless of
    the registry flag, so a marked model would 400 there while working
    everywhere else.
    """

    BUILDER_SOURCES = (
        "anthropic_provider.py",
        "openai_provider.py",
        "azure_provider.py",
        "gemini_provider.py",
        "custom_provider.py",
        "openrouter_provider.py",
        "openai_responses_provider.py",
    )

    def test_no_unconditional_temperature_in_any_provider(self):
        import pathlib

        import kollabor_ai.providers as providers_pkg

        root = pathlib.Path(providers_pkg.__file__).parent
        offenders = []
        for name in self.BUILDER_SOURCES:
            source = (root / name).read_text()
            self.assertIn(
                "sampling_params", source, f"{name} does not use the shared gate"
            )
            # the literal that means "send it no matter what"
            for line in source.splitlines():
                stripped = line.strip()
                if stripped.startswith(("#", '"')):
                    continue
                if '"temperature": self.config.temperature' in stripped:
                    offenders.append(f"{name}: {stripped}")
        self.assertEqual(offenders, [])

    def test_custom_and_openrouter_omit_for_marked_models(self):
        from kollabor_ai.providers.models import CustomConfig, OpenRouterConfig
        from kollabor_ai.providers.tuning import sampling_params

        for config in (
            CustomConfig(
                api_key="k",
                model="claude-opus-5",
                base_url="https://example.com/v1",
                temperature=0.7,
                top_p=0.9,
            ),
            OpenRouterConfig(
                api_key="sk-or-testkey",
                model="claude-opus-5",
                temperature=0.7,
                top_p=0.9,
            ),
        ):
            self.assertEqual(sampling_params(config, config.model), {})
            # ...and still sends them for a model that accepts them
            self.assertIn("temperature", sampling_params(config, "glm-5.2"))


class GeminiSamplingTests(unittest.TestCase):
    def _config(self, model):
        return GeminiConfig(api_key="k", model=model, temperature=0.4)

    def test_generation_config_drops_temperature_when_unsupported(self):
        provider = GeminiProvider(self._config("claude-opus-5"))
        payload = provider._prepare_request(list(MESSAGES), tools=None)
        self.assertNotIn("temperature", payload["generationConfig"])
        self.assertIn("maxOutputTokens", payload["generationConfig"])

    def test_generation_config_keeps_temperature_by_default(self):
        provider = GeminiProvider(self._config("gemini-3.6-flash"))
        payload = provider._prepare_request(list(MESSAGES), tools=None)
        self.assertEqual(payload["generationConfig"]["temperature"], 0.4)


if __name__ == "__main__":
    unittest.main()
