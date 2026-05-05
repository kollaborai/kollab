#!/usr/bin/env bash
# @widget-id: weather
# @name: Weather
# @description: Current weather and temperature
# @category: api
# @refresh: 300s
# @timeout: 10
# @color: true

# Get weather from wttr.in (no-headers format for parsing)
# Using format option for clean output
weather_data=$(curl -s "wttr.in/?format=%C+%t" 2>/dev/null)

if [ -n "$weather_data" ]; then
    echo "$weather_data"
else
    echo "weather: ?"
fi
