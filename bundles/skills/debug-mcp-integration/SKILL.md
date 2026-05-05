---
name: debug-mcp-integration
description: "MCP Integration Troubleshooting skill - diagnose and fix MCP server connections"
---

skill name: debug-mcp-integration

purpose:
  diagnose mcp (model context protocol) server connection issues,
  verify tool availability, and debug tool execution failures.
  mcp enables external tools and services to integrate with the llm.

when to use:
  - mcp tools not appearing in available tools list
  - mcp server fails to start or connect
  - tool execution returns errors
  - server responds but tools are not registered
  - need to verify mcp configuration is correct


methodology:

  phase 1: verify mcp configuration
  phase 2: test server connectivity
  phase 3: inspect tool registration
  phase 4: debug tool execution
  phase 5: analyze logs and errors


phase 1: verify mcp configuration


check mcp settings file locations

mcp configuration is loaded from two locations (local overrides global):

  [1] global: ~/.kollab/mcp/mcp_settings.json
  [2] local:  .kollab/mcp/mcp_settings.json  (takes priority)

verify local config exists:
  <read><file>.kollab/mcp/mcp_settings.json</file></read>

verify global config exists:
  <terminal>cat ~/.kollab/mcp/mcp_settings.json</terminal>

list mcp directory contents:
  <terminal>find .kollab/mcp -type f 2>/dev/null</terminal>
  <terminal>find ~/.kollab/mcp -type f 2>/dev/null</terminal>


understand mcp settings structure

valid mcp_settings.json format:
  {
    "servers": {
      "server-name": {
        "type": "stdio",
        "command": "server-executable --arg1 --arg2",
        "enabled": true
      }
    }
  }

required fields:
  - type: must be "stdio" (http not yet implemented)
  - command: full command to start the server
  - enabled: true/false (optional, defaults to true)

common configuration errors:
  [error] missing "type" field
  [error] command path not executable
  [error] command requires absolute path
  [error] enabled: false (server disabled)
  [error] malformed json


validate configuration syntax

check json validity:
  <terminal>python -c "import json; json.load(open('.kollab/mcp/mcp_settings.json'))"</terminal>

check for syntax errors:
  <terminal>python -m json.tool .kollab/mcp/mcp_settings.json</terminal>

verify each server entry:
  <terminal>grep -A 5 '"servers"' .kollab/mcp/mcp_settings.json</terminal>


verify server commands are executable

for each server in config, test if command exists:
  <terminal>which <server-executable></terminal>

example for npx-based servers:
  <terminal>which npx</terminal>
  <terminal>npx --version</terminal>

example for python-based servers:
  <terminal>which python</terminal>
  <terminal>python -c "import <server-module>"</terminal>


phase 2: test server connectivity


manual server connection test

test starting the server manually:
  <terminal><server-command-from-config></terminal>

if server starts:
  - check for initialization message
  - look for protocol version in output
  - verify it responds to json-rpc

if server fails to start:
  - check command syntax
  - verify dependencies installed
  - check for missing environment variables


test mcp handshake using python

create test script to verify mcp protocol:
  <terminal>python -c "
import asyncio
import json
import sys

async def test_mcp():
    cmd = '<server-command-from-config>'
    proc = await asyncio.create_subprocess_exec(
        *cmd.split(),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )

    # send initialize request
    init_req = {
        'jsonrpc': '2.0',
        'id': '1',
        'method': 'initialize',
        'params': {
            'protocolVersion': '2024-11-05',
            'capabilities': {},
            'clientInfo': {'name': 'test', 'version': '1.0'}
        }
    }

    proc.stdin.write(json.dumps(init_req).encode() + b'\n')
    await proc.stdin.drain()

    # read response
    response = await asyncio.wait_for(proc.stdout.readline(), timeout=5)
    print(response.decode())

    proc.terminate()
    await proc.wait()

asyncio.run(test_mcp())
  "</terminal>


verify server responses

expected initialize response:
  {
    "jsonrpc": "2.0",
    "id": "1",
    "result": {
      "protocolVersion": "2024-11-05",
      "capabilities": {...},
      "serverInfo": {...}
    }
  }

error responses indicate:
  - protocol mismatch
  - server not mcp-compliant
  - invalid client info


phase 3: inspect tool registration


check loaded mcp integration

read the integration code:
  <read><file>kollabor/llm/mcp_integration.py</file><lines>1-100</lines></read>

check initialization logs:
  <terminal>grep -i "mcp" .kollab/logs/kollab.log | tail -20</terminal>

verify servers loaded:
  <terminal>grep "Loaded.*MCP server" .kollab/logs/kollab.log</terminal>

verify tools registered:
  <terminal>grep "Registered MCP tool" .kollab/logs/kollab.log</terminal>


check tool registry state

the tool_registry stores discovered tools:
  - key: tool name
  - value: {server, definition, enabled}

view registered tools in logs:
  <terminal>grep "tools from.*server" .kollab/logs/kollab.log</terminal>

check tool count:
  <terminal>grep "Got.*tools from" .kollab/logs/kollab.log</terminal>


test tool discovery

trigger tool discovery manually:
  <terminal>python -c "
import asyncio
from kollabor.llm.mcp_integration import MCPIntegration

async def test():
    mcp = MCPIntegration()
    print(f'Configured servers: {list(mcp.mcp_servers.keys())}')
    discovered = await mcp.discover_mcp_servers()
    print(f'Discovered: {json.dumps(discovered, indent=2)}')
    print(f'Tool registry: {list(mcp.tool_registry.keys())}')
    await mcp.shutdown()

asyncio.run(test())
  "</terminal>


phase 4: debug tool execution


check tool executor

read tool executor code:
  <read><file>kollabor/llm/tool_executor.py</file><lines>312-374</lines></read>

key execution flow:
  1. llm requests tool call
  2. tool_executor routes to _execute_mcp_tool
  3. mcp_integration.call_mcp_tool invoked
  4. connection.check -> tool call on server
  5. result formatted and returned


verify mcp tool call flow

check mcp call_mcp_tool:
  <read><file>kollabor/llm/mcp_integration.py</file><lines>436-477</lines></read>

verify connection is active:
  <terminal>grep "server_connections" .kollab/logs/kollab.log</terminal>

check for reconnection attempts:
  <terminal>grep "reconnect\|No active connection" .kollab/logs/kollab.log</terminal>


test individual tool call

test a specific tool manually:
  <terminal>python -c "
import asyncio
import json
from kollabor.llm.mcp_integration import MCPIntegration

async def test_tool():
    mcp = MCPIntegration()
    await mcp.discover_mcp_servers()

    tool_name = '<tool-name-to-test>'
    arguments = {}  # add required args

    result = await mcp.call_mcp_tool(tool_name, arguments)
    print(f'Result: {json.dumps(result, indent=2)}')

    await mcp.shutdown()

asyncio.run(test_tool())
  "</terminal>


phase 5: analyze logs and errors


common mcp error patterns

error: "failed to start mcp server"
  causes:
    - command not found
    - missing dependencies
    - permission denied
    - invalid command syntax

  debug:
    <terminal>which <executable-from-command></terminal>
    <terminal><command> --help</terminal>
    <terminal>ls -la $(which <executable>)</terminal>


error: "initialization failed"
  causes:
    - server not mcp-compliant
    - protocol version mismatch
    - server crashed on startup
    - timeout waiting for response

  debug:
    <terminal>grep "initialize.*failed" .kollab/logs/kollab.log</terminal>
    <terminal>grep -A 5 -B 5 "server.*initialized" .kollab/logs/kollab.log</terminal>


error: "tool not found"
  causes:
    - tool name mismatch
    - server not connected
    - tool not registered
    - server disabled

  debug:
    <terminal>grep "tool_registry" .kollab/logs/kollab.log</terminal>
    <terminal>python -c "from kollabor.llm.mcp_integration import MCPIntegration; import asyncio; asyncio.run(MCPIntegration().discover_mcp_servers())"</terminal>


error: "server not initialized"
  causes:
    - handshake not completed
    - connection dropped
    - server crashed after start

  debug:
    <terminal>grep "initialized.*True" .kollab/logs/kollab.log</terminal>
    <terminal>grep "notifications/initialized" .kollab/logs/kollab.log</terminal>


error: "timeout waiting for response"
  causes:
    - server slow to respond
    - tool execution taking too long
    - server hung
    - network issue (for http servers)

  debug:
    check default timeout in mcp_integration.py:413
    verify server responds within timeout
    check for resource constraints


enable detailed mcp logging

add debug logging to investigate:
  <terminal>python -c "
import logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger('kollabor.llm.mcp_integration')
logger.setLevel(logging.DEBUG)
  "</terminal>

run with debug output:
  <terminal>python -c "
import asyncio
import logging
logging.basicConfig(level=logging.DEBUG)

from kollabor.llm.mcp_integration import MCPIntegration

async def test():
    mcp = MCPIntegration()
    await mcp.discover_mcp_servers()
    await mcp.shutdown()

asyncio.run(test())
  "</terminal>


troubleshooting checklist


configuration
  [ ] mcp_settings.json exists and valid json
  [ ] at least one server configured
  [ ] server commands use absolute paths or available in path
  [ ] server type is "stdio"
  [ ] enabled: true (or field omitted)


server availability
  [ ] server executable exists (which/where)
  [ ] server dependencies installed
  [ ] server runs when executed manually
  [ ] server responds to initialize request
  [ ] server sends notifications/initialized


tool registration
  [ ] discover_mcp_servers() completes
  [ ] tools listed in discovered result
  [ ] tools appear in tool_registry
  [ ] tools marked as enabled: true


execution
  [ ] connection remains open after discovery
  [ ] call_mcp_tool() reaches server
  [ ] server responds within timeout
  [ ] result formatted correctly


logs
  [ ] "mcp integration initialized" found
  [ ] "started mcp server process" for each server
  [ ] "mcp server.*initialized" for each server
  [ ] "got n tools from" for each server
  [ ] "registered mcp tool" for each tool


example workflow


scenario: filesystem tools not appearing

step 1: check configuration
  <read><file>.kollab/mcp/mcp_settings.json</file></read>

step 2: verify server command
  <terminal>which npx</terminal>
  <terminal>npx -y @modelcontextprotocol/server-filesystem --help</terminal>

step 3: check logs for loading
  <terminal>grep "filesystem" .kollab/logs/kollab.log</terminal>

step 4: run manual discovery
  <terminal>python -c "
import asyncio
from kollabor.llm.mcp_integration import MCPIntegration

async def test():
    mcp = MCPIntegration()
    print('servers:', list(mcp.mcp_servers.keys()))
    discovered = await mcp.discover_mcp_servers()
    for name, info in discovered.items():
        print(f'{name}: {info[\"tool_count\"]} tools')
    await mcp.shutdown()

asyncio.run(test())
  "</terminal>

step 5: test tool call
  <terminal>python -c "
import asyncio
from kollabor.llm.mcp_integration import MCPIntegration

async def test():
    mcp = MCPIntegration()
    await mcp.discover_mcp_servers()
    result = await mcp.call_mcp_tool('read_file', {'path': '/tmp/test'})
    print(result)
    await mcp.shutdown()

asyncio.run(test())
  "</terminal>


common mcp servers

filesystem server:
  command: npx -y @modelcontextprotocol/server-filesystem /allowed/path
  tools: read_file, write_file, list_directory, directory_tree

git server:
  command: npx -y @modelcontextprotocol/server-git --repository /path/to/repo
  tools: clone, commit, log, status, diff

github server:
  command: mcp-server-github
  tools: create_issue, create_pull_request, get_file

sqlite server:
  command: mcp-server-sqlite --db-path /path/to/database.db
  tools: query, execute, schema

brave search server:
  command: mcp-server-brave-search
  tools: brave_search


expected output

when mcp integration is working correctly:
  [ok] mcp integration initialized
  [ok] loaded n mcp server configurations
  [ok] started mcp server process: <server-name>
  [ok] mcp server <server-name> initialized
  [ok] got n tools from <server-name>
  [ok] registered mcp tool: <tool-name> from <server-name>
  [ok] executed mcp tool: <tool-name>


when issues exist:
  [error] failed to load mcp config
  [warn] failed to start mcp server <name>
  [warn] mcp server <name> initialization failed
  [warn] got 0 tools from <name>
  [error] tool '<name>' not found
  [error] no active connection to server '<name>'


final notes

mcp integration requires:
  - valid json-rpc 2.0 protocol implementation
  - stdio communication with newline-delimited messages
  - initialize handshake before tool operations
  - server must respond within 30 seconds

remember:
  - local config overrides global config
  - disabled servers are skipped
  - connections must stay open for tool calls
  - tools must be registered before use
  - check logs for detailed error traces
