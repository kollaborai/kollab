#!/bin/bash
# Helper script to view raw LLM conversation logs.
#
# Supports two schemas:
#   v1 (schema_version == 1):  profile.{provider,model}, response.content,
#                              turn_id, continuation_of, request.wire_provider
#   v0 (no schema_version):    top-level provider/model, response.content
#
# Both put normalized response text at .response.content and tool_calls at
# .response.tool_calls (same {id, name, input} shape).
#
# Requires: jq

# Pull normalized response content from any schema version
RESPONSE_CONTENT_FILTER='.response.content // ""'

# One-line header per entry: ts | provider/model | turn_id (v1 only)
HEADER_FILTER='
  ((.timestamp // "?") + " | "
   + ((.profile.provider // .provider // "?") | tostring)
   + "/"
   + ((.profile.model // .model // "?") | tostring)
   + (if .schema_version then
       " | turn=" + ((.turn_id // "")[0:8])
       + (if .continuation_of then " (cont " + (.continuation_of[0:8]) + ")" else "" end)
       + (if .request.wire_provider then " | wire=" + .request.wire_provider else "" end)
     else
       ""
     end))
'

# View last response in a conversation file
view_last_response() {
    tail -1 "$1" | jq -r "$RESPONSE_CONTENT_FILTER"
}

# View all responses in a conversation file
view_all_responses() {
    jq -r "$RESPONSE_CONTENT_FILTER" "$1"
}

# View headers (one line per interaction): timestamp, profile, turn id
view_headers() {
    jq -r "$HEADER_FILTER" "$1"
}

# Show wire_request (v1 only) for the last interaction
view_wire() {
    tail -1 "$1" | jq '.request.wire_request // "no wire_request (v0 log or not captured)"'
}

# Count think tags in a response
count_think_tags() {
    local content
    content=$(tail -1 "$1" | jq -r "$RESPONSE_CONTENT_FILTER")
    local opening closing orphaned
    opening=$(echo "$content" | grep -o '<think>' | wc -l | tr -d ' ')
    closing=$(echo "$content" | grep -o '</think>' | wc -l | tr -d ' ')
    orphaned=$((closing - opening))

    echo "File: $(basename "$1")"
    echo "  <think> opening: $opening"
    echo "  </think> closing: $closing"
    echo "  Orphaned: $orphaned"

    if [ "$orphaned" -ne 0 ]; then
        echo "  WARN: ORPHANED TAGS DETECTED"
    fi
}

# Usage examples
case "${1:-help}" in
    "last")
        view_last_response "$2"
        ;;
    "all")
        view_all_responses "$2"
        ;;
    "headers")
        view_headers "$2"
        ;;
    "wire")
        view_wire "$2"
        ;;
    "count")
        count_think_tags "$2"
        ;;
    "scan")
        # Scan a directory of raw logs. Pass dir as $2, optional glob as $3.
        # Default scans the current project's raw conversations dir.
        scan_dir="${2:-$HOME/.kollab/projects}"
        pattern="${3:-*_raw.jsonl}"
        echo "Scanning $scan_dir for $pattern ..."
        # shellcheck disable=SC2044
        for file in $(find "$scan_dir" -type f -name "$pattern" 2>/dev/null); do
            count_think_tags "$file"
            echo ""
        done
        ;;
    *)
        echo "Usage:"
        echo "  $0 last    <file>           - View last response content"
        echo "  $0 all     <file>           - View all response contents"
        echo "  $0 headers <file>           - One-line header per interaction"
        echo "                                (ts | provider/model | turn_id)"
        echo "  $0 wire    <file>           - Show wire_request for last entry (v1 only)"
        echo "  $0 count   <file>           - Count <think> tags in last response"
        echo "  $0 scan    [dir] [pattern]  - Count tags across many files"
        echo "                                (defaults: ~/.kollab/projects, *_raw.jsonl)"
        echo ""
        echo "Examples:"
        echo "  $0 headers ~/.kollab/projects/my_proj/conversations/raw/abc_raw.jsonl"
        echo "  $0 last    ~/.kollab/projects/my_proj/conversations/raw/abc_raw.jsonl"
        echo "  $0 wire    ~/.kollab/projects/my_proj/conversations/raw/abc_raw.jsonl"
        echo "  $0 scan    ~/.kollab/projects/my_proj/conversations/raw"
        ;;
esac
