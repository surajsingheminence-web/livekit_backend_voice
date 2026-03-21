#!/usr/bin/env bash

# Start the voice agent worker in the background
python voice_agent.py start &

# Start the UI web server in the foreground
python ui_server.py