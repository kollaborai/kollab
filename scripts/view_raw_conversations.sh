#!/bin/bash
# Helper script to view raw LLM conversation logs

# View last response in a conversation file
view_last_response() {
    tail -1 "$1" | jq -r '.response.data.choices[0].message.content'
}

# View all responses in a conversation file
view_all_responses() {
    cat "$1" | jq -r '.response.data.choices[0].message.content'
}

# Count think tags in a response
count_think_tags() {
    local content=$(tail -1 "$1" | jq -r '.response.data.choices[0].message.content')
    local opening=$(echo "$content" | grep -o '<think>' | wc -l)
    local closing=$(echo "$content" | grep -o '</think>' | wc -l)
    local orphaned=$((closing - opening))

    echo "File: $(basename $1)"
    echo "  <think> opening: $opening"
    echo "  </think> closing: $closing"
    echo "  Orphaned: $orphaned"

    if [ $orphaned -ne 0 ]; then
        echo "  🚨 ORPHANED TAGS DETECTED!"
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
    "count")
        count_think_tags "$2"
        ;;
    "scan")
        echo "Scanning all recent conversations..."
        for file in .kollab/conversations_raw/raw_llm_interactions_2025-11-07_*.jsonl; do
            count_think_tags "$file"
            echo ""
        done
        ;;
    *)
        echo "Usage:"
        echo "  $0 last <file>   - View last response"
        echo "  $0 all <file>    - View all responses"
        echo "  $0 count <file>  - Count think tags"
        echo "  $0 scan          - Scan all today's conversations"
        echo ""
        echo "Examples:"
        echo "  $0 last .kollab/conversations_raw/raw_llm_interactions_2025-11-07_131824.jsonl"
        echo "  $0 count .kollab/conversations_raw/raw_llm_interactions_2025-11-07_120822.jsonl"
        echo "  $0 scan"
        ;;
esac
