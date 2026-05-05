---
name: validate-config
description: "Configuration integrity validation - check settings, detect conflicts, verify defaults"
---

skill name: validate-config

purpose:
  validate kollabor configuration integrity, check for missing required settings,
  identify conflicting configurations, and show effective (merged) configuration.
  helps diagnose configuration-related issues before they cause runtime problems.


when to use:
  [ ] application behaving unexpectedly
  [ ] plugin not loading properly
  [ ] changes to config.json not taking effect
  [ ] want to understand effective vs default config
  [ ] setting up kollabor for first time
  [ ] migrating between versions
  [ ] diagnosing plugin configuration issues


methodology:

phase 1: config file discovery and structure validation
  locate all config files (local, global, plugin defaults)
  validate json syntax and structure
  check required top-level sections

phase 2: setting validation
  verify required llm settings (api_url, model, etc)
  check plugin configurations
  validate data types and value ranges
  identify deprecated or moved settings

phase 3: conflict detection
  check for duplicate settings with different values
  identify plugin config conflicts
  detect incompatible setting combinations

phase 4: effective config analysis
  show merged configuration (defaults + plugins + user)
  identify which file/layer provides each setting
  highlight user overrides


tools and commands:

core files to read:
  <read><file>kollabor/config/loader.py</file></read>
  <read><file>kollabor/config/manager.py</file></read>
  <read><file>kollabor/config/plugin_config_manager.py</file></read>
  <read><file>kollabor/config/plugin_schema.py</file></read>
  <read><file>kollabor/utils/config_utils.py</file></read>

check config file existence:
  <terminal>ls -la .kollab/config.json 2>/dev/null || echo "no local config"</terminal>
  <terminal>ls -la ~/.kollab/config.json 2>/dev/null || echo "no global config"</terminal>

view config files:
  <terminal>cat .kollab/config.json 2>/dev/null</terminal>
  <terminal>cat ~/.kollab/config.json 2>/dev/null</terminal>

validate json syntax:
  <terminal>python -c "import json; json.load(open('.kollab/config.json'))" 2>&1</terminal>
  <terminal>python -c "import json; json.load(open('~/.kollab/config.json'))" 2>&1</terminal>

find all config-related files:
  <terminal>find .kollab -name "*.json" -type f 2>/dev/null</terminal>
  <terminal>find ~/.kollab -name "*.json" -type f 2>/dev/null</terminal>

check config in logs:
  <terminal>grep -i "config" .kollab/logs/kollab.log | tail -20</terminal>
  <terminal>grep -i "loaded.*config\|merged.*config" .kollab/logs/kollab.log</terminal>


configuration system architecture:

config manager (kollabor/config/manager.py)
  - config_path: path to config.json file
  - config: in-memory configuration dict
  - load_config_file(): load json from file
  - save_config_file(): save json to file
  - get(key_path, default): dot notation access
  - set(key_path, value): dot notation write

config loader (kollabor/config/loader.py)
  - config_manager: base config manager instance
  - plugin_registry: plugin registry for plugin configs
  - plugin_config_manager: dynamic schema manager
  - get_base_config(): returns application defaults
  - get_plugin_configs(): returns merged plugin configs
  - load_complete_config(): returns full merged config
  - _load_user_config_with_fallback(): local -> global priority

config priority order (highest to lowest):
  1. environment variables (kollabor_*)
  2. local config (.kollab/config.json in cwd)
  3. global config (~/.kollab/config.json)
  4. plugin-provided defaults
  5. base application defaults

plugin config manager (kollabor/config/plugin_config_manager.py)
  - plugin_schemas: registered plugin config schemas
  - widget_definitions: ui widget definitions
  - discover_plugin_schemas(): find plugin get_config_schema() methods
  - validate_plugin_config(): validate config against schema
  - get_plugin_default_config(): get defaults for a plugin


phase 1: config file discovery and structure validation

step 1: locate active config file

check which config is being used:
  <terminal>python -c "
from pathlib import path
local = path.cwd() / '.kollab' / 'config.json'
global = path.home() / '.kollab' / 'config.json'
if local.exists():
    print(f'using local: {local}')
elif global.exists():
    print(f'using global: {global}')
else:
    print('no config file found - using defaults')
"</terminal>

check for both configs:
  <terminal>ls -la .kollab/config.json ~/.kollab/config.json 2>&0</terminal>


step 2: validate json syntax

validate local config:
  <terminal>python -c "
import json, sys
try:
    with open('.kollab/config.json') as f:
        data = json.load(f)
    print(f'[ok] valid json, {len(data)} top-level keys')
except json.jsondecodeerror as e:
    print(f'[error] json syntax error: {e}')
    sys.exit(1)
except filenotfounderror:
    print('[warn] no local config file')
"</terminal>

validate global config:
  <terminal>python -c "
import json, sys
from pathlib import path
p = path.home() / '.kollab' / 'config.json'
try:
    with open(p) as f:
        data = json.load(f)
    print(f'[ok] valid json, {len(data)} top-level keys')
except json.jsondecodeerror as e:
    print(f'[error] json syntax error: {e}')
except filenotfounderror:
    print('[warn] no global config file')
"</terminal>


step 3: check required top-level sections

verify config structure:
  <terminal>python -c "
import json
from pathlib import path

required_sections = ['terminal', 'input', 'logging', 'core', 'plugins', 'application']

config_path = None
local = path.cwd() / '.kollab' / 'config.json'
if local.exists():
    config_path = local
else:
    global = path.home() / '.kollab' / 'config.json'
    if global.exists():
        config_path = global

if not config_path:
    print('[warn] no config file found')
else:
    with open(config_path) as f:
        config = json.load(f)

    print('config structure check:')
    for section in required_sections:
        if section in config:
            print(f'  [ok] {section}')
        else:
            print(f'  [warn] {section} - missing (will use defaults)')

    # check for extra/unknown sections
    known = required_sections + ['workflow_enforcement', 'performance', 'hooks']
    for key in config.keys():
        if key not in known:
            print(f'  [info] {key} - custom section')
"</terminal>


phase 2: setting validation

step 1: verify required llm settings

check core llm settings:
  <terminal>python -c "
import json
from pathlib import path

config_path = none
local = path.cwd() / '.kollab' / 'config.json'
if local.exists():
    config_path = local
else:
    global = path.home() / '.kollab' / 'config.json'
    if global.exists():
        config_path = global

# check base defaults from loader
print('checking llm settings...')

required_settings = {
    'core.llm.api_url': 'llm api endpoint url',
    'core.llm.model': 'model name/identifier',
    'core.llm.temperature': 'sampling temperature (0.0-1.0)',
    'core.llm.max_history': 'conversation history limit'
}

if config_path:
    with open(config_path) as f:
        config = json.load(f)

    print('llm settings in config:')
    for key, desc in required_settings.items():
        parts = key.split('.')
        value = config
        try:
            for part in parts:
                value = value[part]
            print(f'  [ok] {key}: {value}')
        except (keyerror, typeerror):
            print(f'  [info] {key}: using default')
else:
    print('  no config file - all settings using defaults')
"</terminal>


step 2: validate setting values

check for invalid values:
  <terminal>python -c "
import json
from pathlib import path

def validate_config(config):
    warnings = []
    errors = []

    # check temperature range
    try:
        temp = config.get('core', {}).get('llm', {}).get('temperature', 0.7)
        if not 0.0 <= temp <= 2.0:
            errors.append(f'invalid temperature: {temp} (must be 0.0-2.0)')
    except: pass

    # check max_history
    try:
        history = config.get('core', {}).get('llm', {}).get('max_history', 90)
        if history < 0 or history > 1000:
            warnings.append(f'unusual max_history: {history}')
    except: pass

    # check timeout values
    try:
        timeout = config.get('core', {}).get('llm', {}).get('timeout', 0)
        if timeout < 0:
            errors.append(f'invalid timeout: {timeout} (must be >= 0)')
    except: pass

    # check render_fps
    try:
        fps = config.get('terminal', {}).get('render_fps', 20)
        if fps < 1 or fps > 120:
            warnings.append(f'unusual render_fps: {fps}')
    except: pass

    return errors, warnings

config_path = none
local = path.cwd() / '.kollab' / 'config.json'
if local.exists():
    config_path = local
else:
    global = path.home() / '.kollab' / 'config.json'
    if global.exists():
        config_path = global

if config_path:
    with open(config_path) as f:
        config = json.load(f)
    errors, warnings = validate_config(config)

    if errors:
        print('errors:')
        for e in errors:
            print(f'  [error] {e}')

    if warnings:
        print('warnings:')
        for w in warnings:
            print(f'  [warn] {w}')

    if not errors and not warnings:
        print('[ok] all setting values valid')
else:
    print('[info] no config file - using defaults')
"</terminal>


step 3: validate plugin configurations

check plugin configs:
  <terminal>python -c "
import json
from pathlib import path

config_path = none
local = path.cwd() / '.kollab' / 'config.json'
if local.exists():
    config_path = local
else:
    global = path.home() / '.kollab' / 'config.json'
    if global.exists():
        config_path = global

known_plugins = [
    'modern_input', 'system_commands', 'hook_monitoring',
    'workflow_enforcement', 'fullscreen'
]

if config_path:
    with open(config_path) as f:
        config = json.load(f)

    plugins = config.get('plugins', {})
    print('plugin configurations:')

    for plugin_name in known_plugins:
        if plugin_name in plugins:
            plugin_config = plugins[plugin_name]
            enabled = plugin_config.get('enabled', false)
            status = '[ok]' if enabled else '[info]'
            print(f'  {status} {plugin_name}: enabled={enabled}')
        else:
            print(f'  [warn] {plugin_name}: not in config (will use defaults)')

    # check for unknown plugins
    for plugin_name in plugins.keys():
        if plugin_name not in known_plugins:
            print(f'  [info] {plugin_name}: custom/unknown plugin')
else:
    print('[info] no config file - all plugins using defaults')
"</terminal>


phase 3: conflict detection

step 1: check for duplicate settings

find settings defined in multiple places:
  <terminal>python -c "
import json
from pathlib import path

def find_all_configs():
    configs = []

    # local config
    local = path.cwd() / '.kollab' / 'config.json'
    if local.exists():
        with open(local) as f:
            configs.append(('local', json.load(f)))

    # global config
    global = path.home() / '.kollab' / 'config.json'
    if global.exists():
        with open(global) as f:
            configs.append(('global', json.load(f)))

    return configs

def flatten_config(d, prefix=''):
    items = []
    for k, v in d.items():
        key = f'{prefix}.{k}' if prefix else k
        if isinstance(v, dict):
            items.extend(flatten_config(v, key))
        else:
            items.append((key, v))
    return items

configs = find_all_configs()

if len(configs) <= 1:
    print('[info] only one config source - no conflicts possible')
else:
    # collect all settings from all configs
    all_settings = {}
    for name, config in configs:
        for key, value in flatten_config(config):
            if key not in all_settings:
                all_settings[key] = []
            all_settings[key].append((name, value))

    # find duplicates with different values
    print('potential conflicts (same key, different value):')
    conflicts_found = false
    for key, sources in all_settings.items():
        if len(sources) > 1:
            values = set(str(v) for _, v in sources)
            if len(values) > 1:
                conflicts_found = true
                print(f'  [warn] {key}:')
                for source, value in sources:
                    print(f'      {source}: {value}')

    if not conflicts_found:
        print('[ok] no conflicting values found')
"</terminal>


step 2: check plugin config conflicts

check for incompatible plugin combinations:
  <terminal>python -c "
import json
from pathlib import path

config_path = none
local = path.cwd() / '.kollab' / 'config.json'
if local.exists():
    config_path = local
else:
    global = path.home() / '.kollab' / 'config.json'
    if global.exists():
        config_path = global

# known incompatible combinations
incompatible_plugins = [
    # workflow_enforcement interferes with normal operation


]

if config_path:
    with open(config_path) as f:
        config = json.load(f)

    plugins = config.get('plugins', {})
    enabled = [name for name, cfg in plugins.items() if cfg.get('enabled', false)]

    print('checking plugin compatibility...')
    for p1, p2 in incompatible_plugins:
        if p1 in enabled and p2 in enabled:
            print(f'  [warn] {p1} and {p2} may not work well together')

    # check for duplicate-like plugins
    input_plugins = [p for p in enabled if 'input' in p.lower()]
    if len(input_plugins) > 1:
        print(f'  [warn] multiple input-related plugins: {input_plugins}')

    print('[ok] plugin compatibility check complete')
else:
    print('[info] no config file')
"</terminal>


phase 4: effective config analysis

step 1: show merged configuration structure

display effective config:
  <terminal>python -c "
import sys
sys.path.insert(0, '.')

from kollabor.config.loader import configloader
from kollabor.config.manager import configmanager
from pathlib import path

# create config loader
local = path.cwd() / '.kollab' / 'config.json'
manager = configmanager(local)
loader = configloader(manager)

# get complete merged config
config = loader.load_complete_config()

print('effective configuration (merged):')
print('=' * 50)

# show top-level keys
for key in sorted(config.keys()):
    value = config[key]
    if isinstance(value, dict):
        print(f'{key}:')
        for subkey in sorted(value.keys())[:5]:  # show first 5
            print(f'  - {subkey}')
        if len(value) > 5:
            print(f'  ... and {len(value) - 5} more')
    else:
        print(f'{key}: {value}')
"</terminal>


step 2: trace setting sources

find where a setting comes from:
  <terminal>python -c "
import sys
sys.path.insert(0, '.')

from kollabor.config.loader import configloader
from kollabor.config.manager import configmanager
from pathlib import path
import json

# check each layer
def get_setting_path(config_dict, path_parts):
    value = config_dict
    for part in path_parts:
        if isinstance(value, dict) and part in value:
            value = value[part]
        else:
            return none
    return value

setting = 'core.llm.api_url'  # change this to check different settings
parts = setting.split('.')

print(f'tracing setting: {setting}')
print('-' * 40)

# check base defaults
print('[1] base defaults:')
loader_instance = configloader(configmanager(path.cwd() / '.kollab' / 'config.json'))
base = loader_instance.get_base_config()
value = get_setting_path(base, parts)
if value is not none:
    print(f'    value: {value}')

# check user config
print('[2] user config:')
local = path.cwd() / '.kollab' / 'config.json'
if local.exists():
    with open(local) as f:
        user = json.load(f)
    value = get_setting_path(user, parts)
    if value is not none:
        print(f'    value: {value}')
        print(f'    source: {local}')
else:
    print('    no local config')

# final effective value
print('[3] effective value:')
effective = loader_instance.load_complete_config()
value = get_setting_path(effective, parts)
if value is not none:
    print(f'    {value}')
"</terminal>


step 3: compare config to defaults

show user overrides:
  <terminal>python -c "
import sys
sys.path.insert(0, '.')

from kollabor.config.loader import configloader
from kollabor.config.manager import configmanager
from pathlib import path
import json

def compare_configs(default, user):
    differences = []

    def flatten(d, prefix=''):
        items = {}
        for k, v in d.items():
            key = f'{prefix}.{k}' if prefix else k
            if isinstance(v, dict):
                items.update(flatten(v, key))
            else:
                items[key] = v
        return items

    flat_default = flatten(default)
    flat_user = flatten(user)

    # find overrides
    for key in flat_user:
        if key in flat_default:
            if flat_default[key] != flat_user[key]:
                differences.append({
                    'key': key,
                    'default': flat_default[key],
                    'user': flat_user[key]
                })
        else:
            differences.append({
                'key': key,
                'default': '(not set)',
                'user': flat_user[key]
            })

    return differences

print('user overrides vs defaults:')
print('=' * 50)

loader = configloader(configmanager(path.cwd() / '.kollab' / 'config.json'))
default = loader.get_base_config()

local = path.cwd() / '.kollab' / 'config.json'
if local.exists():
    with open(local) as f:
        user = json.load(f)

    diffs = compare_configs(default, user)

    if diffs:
        for diff in diffs[:20]:  # show first 20
            print(f"{diff['key']}:")
            print(f"  default: {diff['default']}")
            print(f"  user:    {diff['user']}")

        if len(diffs) > 20:
            print(f'... and {len(diffs) - 20} more differences')
    else:
        print('[ok] config matches defaults')
else:
    print('[info] no local config file')
"</terminal>


example workflow:

scenario: "kollabor not connecting to llm"

step 1: check config file exists
  <terminal>ls -la .kollab/config.json ~/.kollab/config.json</terminal>

step 2: verify llm settings in config
  <terminal>python -c "
import json
from pathlib import path

for p in [path.cwd() / '.kollab' / 'config.json', path.home() / '.kollab' / 'config.json']:
    if p.exists():
        with open(p) as f:
            cfg = json.load(f)
        llm = cfg.get('core', {}).get('llm', {})
        print(f'file: {p}')
        print(f'  api_url: {llm.get(\"api_url\", \"(default)\")}')
        print(f'  model: {llm.get(\"model\", \"(default)\")}')
        print(f'  api_token: {\"(set)\" if llm.get(\"api_token\") else \"(empty)\"}')
        print()
"</terminal>

step 3: check for syntax errors
  <terminal>python -c "import json; json.load(open('.kollab/config.json'))"</terminal>

step 4: verify effective config
  <terminal>python -c "
import sys
sys.path.insert(0, '.')
from kollabor.config.loader import configloader
from kollabor.config.manager import configmanager
from pathlib import path

loader = configloader(configmanager(path.cwd() / '.kollab' / 'config.json'))
cfg = loader.load_complete_config()
print('effective api_url:', cfg['core']['llm']['api_url'])
"</terminal>


example workflow 2:

scenario: "plugin configuration not taking effect"

step 1: check plugin in config
  <terminal>grep -a 10 '"enhanced_input"' .kollab/config.json</terminal>

step 2: verify enabled status
  <terminal>python -c "
import json
with open('.kollab/config.json') as f:
    cfg = json.load(f)
print('enhanced_input enabled:', cfg.get('plugins', {}).get('enhanced_input', {}).get('enabled', false))
"</terminal>

step 3: check for plugin config errors in logs
  <terminal>grep -i "plugin.*error\|plugin.*fail" .kollab/logs/kollab.log | tail -20</terminal>


troubleshooting tips:

tip 1: config changes not taking effect
  - verify editing correct config file (check local vs global)
  - json syntax errors will cause silent fallback to defaults
  - some settings require application restart
  - check logs for config loading errors

tip 2: missing expected settings
  - not all settings need to be in config.json
  - defaults come from kollabor/config/loader.py:get_base_config()
  - plugins can provide defaults via get_config_schema()
  - check logs for "using default" messages

tip 3: plugin configuration issues
  - plugins must be registered in plugins section
  - each plugin needs "enabled" boolean
  - plugin configs merge with defaults, don't need all keys
  - check plugin's get_default_config() for expected structure

tip 4: understanding priority
  - local (.kollab/config.json) overrides global (~/.kollab/config.json)
  - user config overrides plugin defaults
  - plugin defaults override base defaults
  - env vars (kollabor_*) override everything

tip 5: debugging config loading
  - set log level to debug: "logging": {"level": "debug"}
  - check logs for "loaded config" messages
  - look for "merged configuration" entries
  - errors during config load fall back to defaults silently

tip 6: common config mistakes
  - trailing comma in json (invalid syntax)
  - using single quotes instead of double quotes
  - missing closing brace }
  - wrong data type (string instead of number)
  - incorrect nested structure (core.llm vs core:llm)


expected output:

when this skill executes successfully, you should be able to:

  [ ] locate and identify active config file
  [ ] validate json syntax of config files
  [ ] verify required settings are present
  [ ] detect conflicting configuration values
  [ ] understand effective vs default configuration
  [ ] trace where each setting comes from
  [ ] identify user overrides vs defaults
  [ ] diagnose plugin configuration issues


quick validation commands:

basic health check:
  <terminal>python -c "
import sys, json
from pathlib import path

checks = []

# check local config
local = path.cwd() / '.kollab' / 'config.json'
if local.exists():
    try:
        with open(local) as f:
            json.load(f)
        checks.append(('[ok]', 'local config exists and valid'))
    except json.jsondecodeerror as e:
        checks.append(('[error]', f'local config has invalid json: {e}'))
    except exception as e:
        checks.append(('[error]', f'local config error: {e}'))
else:
    checks.append(('[info]', 'no local config'))

# check global config
global = path.home() / '.kollab' / 'config.json'
if global.exists():
    try:
        with open(global) as f:
            json.load(f)
        checks.append(('[ok]', 'global config exists and valid'))
    except json.jsondecodeerror as e:
        checks.append(('[error]', f'global config has invalid json: {e}'))
    except exception as e:
        checks.append(('[error]', f'global config error: {e}'))
else:
    checks.append(('[info]', 'no global config'))

# print results
for status, msg in checks:
    print(f'{status} {msg}')
"</terminal>

full validation report:
  <terminal>python -c "
import sys
sys.path.insert(0, '.')
from pathlib import path
import json

print('=' * 60)
print('kollabor configuration validation report')
print('=' * 60)

# check config files
local = path.cwd() / '.kollab' / 'config.json'
global = path.home() / '.kollab' / 'config.json'

print('\n[1] config files:')
if local.exists():
    print('  [ok] local config: .kollab/config.json')
else:
    print('  [info] no local config')

if global.exists():
    print('  [ok] global config: ~/.kollab/config.json')
else:
    print('  [info] no global config')

# validate syntax
print('\n[2] json syntax:')
for name, path in [('local', local), ('global', global)]:
    if path.exists():
        try:
            with open(path) as f:
                data = json.load(f)
            print(f'  [ok] {name}: valid json')
        except json.jsondecodeerror as e:
            print(f'  [error] {name}: {e}')

# check structure
print('\n[3] config structure:')
config_path = local if local.exists() else (global if global.exists() else none)
if config_path:
    with open(config_path) as f:
        config = json.load(f)

    required = ['terminal', 'input', 'logging', 'core', 'plugins']
    for section in required:
        if section in config:
            print(f'  [ok] {section}')
        else:
            print(f'  [warn] {section}: missing (defaults used)')

print('\n[4] summary:')
if local.exists() or global.exists():
    print('  [ok] configuration file(s) found')
    print('  run specific validation phases for detailed analysis')
else:
    print('  [info] no config files - using application defaults')
    print('  config is optional, defaults will be used')

print('\n' + '=' * 60)
"</terminal>


status tags reference:

  [ok]   configuration is valid
  [warn] potential issue that should be reviewed
  [error] definite problem that needs fixing
  [info] informational message
  [todo]  action required

common exit conditions:

  [ok]   all validations passed
  [ok]   issues identified and resolved
  [warn] non-critical issues found
  [error] critical configuration errors found
