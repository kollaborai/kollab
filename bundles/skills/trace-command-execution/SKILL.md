---
name: trace-command-execution
description: "Traces slash command execution from input to handler completion"
---

trace-command-execution skill

purpose:
  trace the complete execution path of slash commands from user input
  through parsing, registry lookup, handler execution, and result display.
  helps debug why commands fail or behave unexpectedly.


when to use:
  - a slash command fails with "unknown command" error
  - command handler is not being called
  - need to find which plugin registered a command
  - command executes but produces wrong output
  - need to understand command execution flow
  - debugging command alias conflicts


methodology:
  1. verify command parsing - check raw input is parsed correctly
  2. check registry state - verify command is registered
  3. trace handler lookup - find which handler will execute
  4. follow execution path - track through executor
  5. inspect result - verify command result is correct


tools and commands:

  list all registered commands:
    <read><file>kollabor/commands/registry.py</file></read>
    <terminal>grep -r "register_command" kollabor/ plugins/ --include="*.py"</terminal>

  check specific command registration:
    <terminal>grep -r "name=\"help\"" kollabor/ plugins/ --include="*.py"</terminal>
    <terminal>grep -r "CommandDefinition" kollabor/commands/ --include="*.py" -A 5</terminal>

  trace parser behavior:
    <read><file>kollabor/commands/parser.py</file></read>
    <terminal>python -c "from kollabor.commands.parser import SlashCommandParser; p = SlashCommandParser(); print(p.parse_command('/help test'))"</terminal>

  inspect executor flow:
    <read><file>kollabor/commands/executor.py</file></read>
    <terminal>grep -A 10 "execute_command" kollabor/commands/executor.py</terminal>

  view plugin command registration:
    <read><file>plugins/system_commands_plugin.py</file></read>
    <terminal>grep -r "def register_commands" plugins/ --include="*.py" -A 20</terminal>

  check event types for command lifecycle:
    <read><file>kollabor/events/models.py</file><lines>80-90</lines></read>
    <terminal>grep -E "SLASH_COMMAND|COMMAND_" kollabor/events/models.py</terminal>

  find handler for a specific command:
    <terminal>grep -r "handle_help" kollabor/ --include="*.py"</terminal>
    <terminal>grep -r "def handle_" kollabor/commands/ --include="*.py"</terminal>


example workflow:

  scenario: /mycommand returns "unknown command"

  step 1 - verify parsing:
    <terminal>python3 -c "
from kollabor.commands.parser import SlashCommandParser
parser = SlashCommandParser()
cmd = parser.parse_command('/mycommand arg1 arg2')
if cmd:
    print(f'parsed: name={cmd.name}, args={cmd.args}')
else:
    print('parse failed')
"</terminal>

  step 2 - check registry:
    <terminal>python3 -c "
from kollabor.commands.registry import SlashCommandRegistry
registry = SlashCommandRegistry()
cmd_def = registry.get_command('mycommand')
if cmd_def:
    print(f'found: plugin={cmd_def.plugin_name}, enabled={cmd_def.enabled}')
else:
    print('command not in registry')
"</terminal>

  step 3 - find where command should be registered:
    <terminal>grep -r "mycommand" plugins/ --include="*.py"</terminal>
    <terminal>grep -r "name=\"mycommand\"" . --include="*.py"</terminal>

  step 4 - check plugin initialization:
    <read><file>plugins/system_commands_plugin.py</file><lines>40-60</lines></read>
    <terminal>grep "register_commands" plugins/*.py</terminal>

  step 5 - trace execution if registered:
    <terminal>grep -A 30 "async def execute_command" kollabor/commands/executor.py</terminal>


  scenario: command executes but returns wrong result

  step 1 - find handler:
    <terminal>grep -r "handle_mycommand" . --include="*.py"</terminal>

  step 2 - inspect handler logic:
    <read><file>path/to/handler_file.py</file><lines>start-end</lines></read>

  step 3 - add debug logging:
    in handler, add: logger.info(f"[TRACE] mycommand called with args={command.args}")

  step 4 - check command result structure:
    <read><file>kollabor/events/models.py</file><lines>253-262</lines></read>


expected output:

  successful trace shows:
    [ok] parser: /help -> SlashCommand(name='help', args=[], raw_input='/help')
    [ok] registry: found command 'help' in plugin 'system'
    [ok] executor: handler=handle_help, mode=INSTANT, enabled=True
    [ok] result: CommandResult(success=True, message='...')


troubleshooting tips:

  command not found:
    - check plugin is loaded (grep plugin name in plugins/)
    - verify register_command() was called
    - check command name matches (case-sensitive)
    - look for registration errors in logs

  handler not executing:
    - verify handler is callable (async def or def)
    - check command.enabled is True
    - look for exceptions in executor.py logs
    - verify event bus is working

  wrong output:
    - check handler return value is CommandResult
    - verify display_type is one of: info, success, warning, error
    - inspect message field construction

  alias not working:
    - verify alias is in aliases list during registration
    - check for alias conflicts in registry
    - test with primary command name

  modal not showing:
    - verify ui_config.type is set correctly
    - check modal_config is populated
    - verify event bus emits MODAL_TRIGGER event


key files to understand command system:

  kollabor/commands/parser.py       - parses /command from user input
  kollabor/commands/registry.py     - stores all command definitions
  kollabor/commands/executor.py     - executes command handlers
  kollabor/events/models.py         - CommandDefinition, SlashCommand, CommandResult
  plugins/system_commands_plugin.py - example of command registration


quick reference - command lifecycle:

  1. user types "/help topic"
  2. SlashCommandParser.parse_command() -> SlashCommand(name='help', args=['topic'])
  3. SlashCommandExecutor.execute_command() called
  4. emits SLASH_COMMAND_DETECTED event
  5. registry.get_command('help') -> CommandDefinition
  6. checks command.enabled
  7. emits SLASH_COMMAND_EXECUTE event
  8. calls handler(SlashCommand) -> CommandResult
  9. emits SLASH_COMMAND_COMPLETE event
  10. displays result via COMMAND_OUTPUT_DISPLAY event


status tags:
  [ok]     - command found and will execute
  [warn]   - command found but may have issues
  [error]  - command not found or registration failed
  [todo]   - investigation step needed
