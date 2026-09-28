"""Speech output uses the same device queue as automatic voice replies."""

from ..tool_definition import ToolDefinition, ToolParameter
from ..tool_registry import get_registry


def register_all():
    get_registry().register(
        ToolDefinition(
            name="voice-out",
            xml_tag="voice_out",
            category="voice",
            description="Speak an intentional update during an active voice turn through the shared device queue.",
            parameters=[
                ToolParameter(
                    name="text",
                    type="string",
                    description=(
                        "Only spoken narration: one or two short natural sentences, "
                        "at most 50 words. Never code, tool output, or display text."
                    ),
                    required=True,
                )
            ],
            xml_form="body",
            xml_body_param="text",
            requires_permission=False,
            examples=[
                "<voice_out>The tests are still running. I will report the result.</voice_out>"
            ],
            key_rules=[
                "Use only for an active voice conversation. Never activate a microphone from a tool.",
                "Speech is classified before playback. If rejected, follow the returned nudge and shorten it once.",
                "The final reply is spoken automatically. Explicit speech and the final "
                "reply share a reply ID, preventing duplicate playback.",
            ],
        )
    )


register_all()
