#!/usr/bin/env bash
# @widget-id: uptime
# @name: Uptime
# @description: System uptime since boot
# @category: system
# @refresh: 60s
# @color: true

# Get uptime in a cross-platform way
if [[ "$OSTYPE" == "darwin"* ]]; then
    # macOS
    boot=$(sysctl -n kern.boottime | awk -F'[ ,]' '{print $4}')
    now=$(date +%s)
    uptime=$((now - boot))
else
    # Linux
    uptime=$(cat /proc/uptime | awk '{print int($1)}')
fi

# Format uptime
days=$((uptime / 86400))
hours=$(((uptime % 86400) / 3600))
minutes=$(((uptime % 3600) / 60))

if [ $days -gt 0 ]; then
    echo "up: ${days}d ${hours}h ${minutes}m"
elif [ $hours -gt 0 ]; then
    echo "up: ${hours}h ${minutes}m"
else
    echo "up: ${minutes}m"
fi
