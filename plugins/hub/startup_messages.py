"""Small, rotating startup guidance for the Hub operator."""

from __future__ import annotations

import time

HUB_STARTUP_TIPS: tuple[str, ...] = (
    "Use `/hub status` to see the current mesh and each agent's state.",
    "Use `/hub agents` for a compact list of online agents.",
    "Send a direct message with `/hub msg <agent> <message>`.",
    "Broadcast a message with `/hub broadcast <message>`.",
    "Stop every peer with `/hub stop all`.",
    "Stop one peer with `/hub stop <identity>`.",
    "Ask an agent to work by sending a direct Hub message.",
    'Use `<hub_msg to="agent">...</hub_msg>` when the model needs to message a peer.',
    'Use `<hub_reply to="agent">...</hub_reply>` to keep a Hub conversation threaded.',
    "Use `/hub whoami` to confirm the current agent identity.",
    "Use `/hub console` for a live agent-management view.",
    "Use `/hub feed` to watch agent activity as it happens.",
    "Use `/hub capture all` to inspect recent peer output.",
    "Use `/hub wake <identity>` to wake a parked agent.",
    "Use `/hub pending` to inspect work waiting for review.",
    "Use `/hub tasks list` to inspect persisted task cards.",
    "Use `/hub tasks mine` to focus on tasks assigned to this agent.",
    "Use `/hub metrics` to inspect Hub loop-prevention counters.",
    "Use `/hub spawn <name> <task>` to start a pool agent.",
    "Use `/hub orgs` to see bundled organization templates.",
    "Use `/hub org <name>` to launch an organization.",
    "Use `/hub cron list` to inspect recurring Hub jobs.",
    "Use `/hub notify status` to inspect desktop notification settings.",
    "Use `/hub bridge status` to inspect external messaging bridges.",
    "Use `/hub dns resolve <identity>` to inspect Hub identity records.",
    "Use `/model` to open the model picker.",
    "Use `/model set <name>` to switch models from the command line.",
    "Use `/llm` to browse and activate a saved model loadout.",
    "Model switches are announced to live Hub peers.",
    "New-agent notices include the active model, provider, and profile.",
    "Paste an image directly into the input bar to include it in a message.",
    "Add text before or after a pasted image to send a mixed message.",
    "Slash commands stay local instead of being broadcast as chat.",
    "Use a direct target when only one agent needs the message.",
    "Use a broadcast when every peer needs the same update.",
    "Keep task assignments specific so the receiving agent can act immediately.",
    "Include a file path and acceptance check in a work request.",
    "Use task completion reports to route work into review.",
    "Use task snoozes when work is valid but not ready for a reminder.",
    "Check the roster after starting a new agent to confirm it is online.",
    "The startup screen shows the current agent identity and peer list.",
    "Lifecycle notices are visible without forcing an idle peer to answer.",
    "Use `/hub stop all` before closing a coordinator session with active peers.",
    "Use `/hub status` after a restart to confirm the mesh recovered.",
    "Use `/hub msg` for a question and `/hub tasks` for durable work.",
    "Keep human-facing status updates short; put detail in the task report.",
    "Use model names exactly as shown by `/model list`.",
    "Use `/profile` when you need to inspect provider profile settings.",
    "Use `/mcp` to inspect or configure external tool servers.",
    "Use `/permissions` to review tool approval behavior.",
)

HUB_NEW_FEATURES: tuple[str, ...] = (
    "Paste images directly into the input bar, including mixed text/image messages.",
    "See an agent's active model, provider, and profile when it joins.",
    "See model-switch notices in the Hub stream without waking peer models.",
    "Stop the entire mesh with one clear command: `/hub stop all`.",
)


def choose_startup_tip(seed: int | None = None) -> str:
    """Choose one tip without putting the full catalog in the startup UI."""
    if not HUB_STARTUP_TIPS:
        return "Use `/hub status` to inspect the current mesh."
    value = time.time_ns() if seed is None else seed
    return HUB_STARTUP_TIPS[value % len(HUB_STARTUP_TIPS)]
