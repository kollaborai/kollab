---
name: analyze-plugin-lifecycle
description: "Trace plugin initialization, lifecycle, and shutdown events in Kollab."
---

Trace plugin initialization, lifecycle, and shutdown events in Kollab.

skill name: analyze-plugin-lifecycle

purpose:
  diagnose plugin-related issues by tracing the complete plugin lifecycle from
  discovery through loading, initialization, hook registration, and shutdown.
  helps identify why plugins fail to load, initialize incorrectly, or cause
  startup problems.

when to use:
  - a plugin is not loading or showing up
  - application fails during startup with plugin-related errors
  - need to verify plugin initialization order
  - investigating plugin shutdown issues
  - want to see which hooks a plugin has registered
  - debugging plugin dependency or configuration problems

methodology:

  step 1: check plugin discovery
    verify plugins are being discovered from the plugins directory.
    check both package installation directory and development fallback.

  step 2: examine plugin loading
    review which plugin modules were successfully loaded and which failed.
    check for import errors or security validation failures.

  step 3: trace plugin instantiation
    verify plugin classes were instantiated with correct dependencies.
    check factory errors and instantiation results.

  step 4: verify plugin initialization
    confirm initialize() was called on each plugin instance.
    check for errors during plugin initialization.

  step 5: audit hook registration
    review which hooks each plugin registered.
    verify hook priority and event type bindings.

  step 6: check shutdown sequence
    verify plugins shutdown cleanly on application exit.

tools and commands:

  files to read:
    - kollabor/plugins/discovery.py       (file scanning, module loading, security)
    - kollabor/plugins/factory.py         (instantiation, init, shutdown)
    - kollabor/plugins/registry.py        (coordinates discovery, factory, collector)
    - kollabor/plugins/collector.py       (status collection)
    - kollabor/application.py             (plugin orchestration in __init__, start, shutdown)

  log inspection:
    - .kollab/logs/kollab.log (plugin lifecycle messages)

  grep patterns:
    - "Plugin.*initialized"           (plugin initialization)
    - "Loaded plugin class"           (successful plugin loading)
    - "Failed to.*plugin"             (plugin failures)
    - "Registered hooks for"          (hook registration)
    - "shutdown plugin"               (plugin shutdown)

  python inspection:
    - inspect PluginDiscovery.get_discovery_stats()
    - inspect PluginFactory.get_factory_stats()
    - inspect PluginRegistry.get_registry_stats()

example workflow:

  scenario: plugin not loading

  1. check logs for plugin discovery:
     grep "Discovered.*plugin" .kollab/logs/kollab.log

  2. verify plugin file exists and is named correctly:
     ls plugins/*_plugin.py
     (must end with _plugin.py)

  3. check for loading errors:
     grep -i "error.*plugin" .kollab/logs/kollab.log

  4. verify plugin class structure:
     grep "class.*Plugin" plugins/your_plugin.py
     grep "def get_default_config" plugins/your_plugin.py

  5. check security validation:
     grep "Skipping invalid plugin" .kollab/logs/kollab.log
     grep "Plugin location verification failed" .kollab/logs/kollab.log

expected output:

  plugin discovery summary:
    [ok] plugins directory: /path/to/plugins
    [ok] discovered modules: 8
    [ok] loaded classes: 8
    [ok] plugins with config: 6

  plugin instantiation:
    [ok] hookmonitoringplugin -> HookMonitoringPlugin
    [ok] enhancedinputplugin -> EnhancedInputPlugin
    [ok] systemcommandsplugin -> SystemCommandsPlugin

  initialization status:
    [ok] HookMonitoringPlugin: initialize() called
    [ok] EnhancedInputPlugin: initialize() called
    [warn] CustomPlugin: initialize() method not found

  hook registration:
    [ok] HookMonitoringPlugin: 12 hooks registered
    [ok] EnhancedInputPlugin: 5 hooks registered

troubleshooting tips:

  plugin not discovered:
    - verify filename ends with _plugin.py
    - check file is in plugins/ directory
    - verify filename contains only alphanumeric chars and underscores
    - check logs for security validation failures

  plugin fails to load:
    - check for syntax errors in plugin file
    - verify all imports are available
    - check get_default_config() method exists
    - look for import errors in logs

  plugin fails to initialize:
    - verify initialize() method is async
    - check initialize() signature accepts kwargs
    - look for exceptions in plugin initialize() code
    - check required dependencies are available

  plugin hooks not firing:
    - verify register_hooks() method exists and is async
    - check hooks are registered with correct event types
    - verify hook priority is set correctly
    - check event bus is properly passed to plugin

  plugin shutdown issues:
    - verify shutdown() method is async
    - check for exceptions during shutdown
    - ensure plugin cleans up resources properly

key implementation details:

  plugin discovery order:
    1. PluginRegistry initialized with plugins_dir
    2. PluginDiscovery.scan_plugin_files() finds *_plugin.py files
    3. security validation (_sanitize_plugin_name, _verify_plugin_location)
    4. load_module() imports and extracts plugin classes
    5. classes must: end with 'Plugin', have get_default_config()

  instantiation flow:
    1. PluginFactory.instantiate_all() called
    2. each plugin class instantiated with: name, state_manager, event_bus,
       renderer, config
    3. instances stored in factory.plugin_instances dict
    4. duplicate storage: both "PluginName" and "pluginname" keys

  initialization flow:
    1. application._initialize_plugins() called after llm service init
    2. for each unique instance (by id):
       - if initialize() exists: call with kwargs
       - if register_hooks() exists: call to register event hooks
    3. system commands hooks registered last

  shutdown flow:
    1. cleanup() called from finally block
    2. background tasks cancelled
    3. running = False, startup_complete = False
    4. shutdown() method:
       - input_handler.stop()
       - llm_service.shutdown()
       - for each plugin: if shutdown() exists, await it
       - clear terminal, restore state
       - state_manager.close()
