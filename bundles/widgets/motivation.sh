#!/usr/bin/env bash
# @widget-id: motivation
# @name: Motivation
# @description: Encouraging messages
# @category: fun
# @refresh: 30s
# @color: true

messages=(
    "Keep coding!"
    "You got this!"
    "Stay focused!"
    "Almost there!"
    "Great work!"
    "Push forward!"
    "Stay curious!"
    "Build cool stuff!"
    "Ship it!"
    "Debug later!"
)

# Use minute of hour to rotate messages
minute=$(date +%M)
index=$((minute % ${#messages[@]}))
echo "${messages[$index]}"
