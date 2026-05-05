#!/usr/bin/env python3
"""Quick test to check command menu contains /example."""

import logging
import sys
from pathlib import Path

# Add core to path
sys.path.insert(0, str(Path(__file__).parent))

# Set up logging to see what's happening
logging.basicConfig(
    level=logging.DEBUG,
    format="%(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)

from kollabor.commands.registry import SlashCommandRegistry
from kollabor.fullscreen.command_integration import (
    FullScreenCommandIntegrator,
)
from kollabor_events.bus import EventBus

# Create minimal setup
event_bus = EventBus()
registry = SlashCommandRegistry()

# Create integrator
integrator = FullScreenCommandIntegrator(
    command_registry=registry,
    event_bus=event_bus,
    config=None,
    profile_manager=None,
    terminal_renderer=None,  # Can be None for discovery test
)

# Discover plugins
plugins_dir = Path(__file__).parent / "plugins"
count = integrator.discover_and_register_plugins(plugins_dir)

print(f"\nDiscovered {count} fullscreen plugins")
print(f"Registered plugins: {integrator.get_registered_plugins()}")

# Check what's in the registry
all_commands = registry.get_all_commands()
print(f"\nTotal commands in registry: {len(all_commands)}")

print("\nLooking for fullscreen commands:")
for cmd in all_commands:
    if cmd.get("plugin_name") == "fullscreen_integrator":
        print(f"  ✓ {cmd['name']} - {cmd.get('description', 'No description')}")
        print(f"    Plugin: {cmd.get('plugin_name')}")
        print(f"    Category: {cmd.get('category')}")
        print(f"    Aliases: {cmd.get('aliases', [])}")

# Test filtering for "example"
filtered = registry.list_commands()
print(f"\nFiltering for 'example': {len(filtered)} results")
for cmd in filtered:
    print(f"  - {cmd['name']}: {cmd.get('description', '')}")
