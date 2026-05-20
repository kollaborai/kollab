#!/bin/bash
# ============================================================================
# Mock MCP Server for Testing
# ============================================================================
# A simple stdio-based mock MCP server that responds to basic MCP protocol
# messages. Use this for testing MCP integration without real servers.
#
# Usage:
#   ./mock_mcp_server.sh
#
# Configure in ~/.kollab/mcp/mcp_settings.json:
#   {
#     "servers": {
#       "mock-test": {
#         "type": "stdio",
#         "command": "tests/tmux/mock_mcp_server.sh",
#         "enabled": true,
#         "description": "Mock MCP server for testing"
#       }
#     }
#   }
# ============================================================================

# Log to stderr for debugging
log() {
    echo "[MOCK-MCP] $1" >&2
}

json_field() {
    python3 - "$1" "$2" <<'PY'
import json
import sys

try:
    obj = json.loads(sys.argv[1])
except Exception:
    obj = {}

field = sys.argv[2]
if field == "method":
    print(obj.get("method", ""))
elif field == "id":
    if "id" in obj:
        print(json.dumps(obj["id"]))
    else:
        print("")
elif field == "tool_name":
    params = obj.get("params") or {}
    print(params.get("name", ""))
PY
}

log "Mock MCP server starting..."

# Read JSON-RPC messages from stdin and respond
while IFS= read -r line; do
    # Skip empty lines
    [ -z "$line" ] && continue

    log "Received: $line"

    method=$(json_field "$line" method)
    id=$(json_field "$line" id)

    log "Method: $method, ID: $id"

    case "$method" in
        "initialize")
            # Respond to initialize
            response='{
                "jsonrpc": "2.0",
                "id": '"${id:-1}"',
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "tools": {}
                    },
                    "serverInfo": {
                        "name": "mock-mcp-server",
                        "version": "1.0.0"
                    }
                }
            }'
            echo "$response" | tr -d '\n'
            echo ""
            log "Sent initialize response"
            ;;

        "tools/list")
            # Return mock tools
            response='{
                "jsonrpc": "2.0",
                "id": '"${id:-1}"',
                "result": {
                    "tools": [
                        {
                            "name": "mock_echo",
                            "description": "Echo back input for testing",
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "message": {
                                        "type": "string",
                                        "description": "Message to echo"
                                    }
                                },
                                "required": ["message"]
                            }
                        },
                        {
                            "name": "mock_test",
                            "description": "Test tool that always succeeds",
                            "inputSchema": {
                                "type": "object",
                                "properties": {}
                            }
                        }
                    ]
                }
            }'
            echo "$response" | tr -d '\n'
            echo ""
            log "Sent tools/list response"
            ;;

        "tools/call")
            # Handle tool calls
            tool_name=$(json_field "$line" tool_name)
            log "Tool call: $tool_name"

            response='{
                "jsonrpc": "2.0",
                "id": '"${id:-1}"',
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": "Mock response from '"$tool_name"'"
                        }
                    ]
                }
            }'
            echo "$response" | tr -d '\n'
            echo ""
            log "Sent tools/call response"
            ;;

        "notifications/initialized")
            # No response needed for notifications
            log "Received initialized notification"
            ;;

        *)
            # Unknown method - return error
            if [ -n "$id" ]; then
                response='{
                    "jsonrpc": "2.0",
                    "id": '"${id}"',
                    "error": {
                        "code": -32601,
                        "message": "Method not found: '"$method"'"
                    }
                }'
                echo "$response" | tr -d '\n'
                echo ""
                log "Sent error response for unknown method"
            fi
            ;;
    esac
done

log "Mock MCP server shutting down"
