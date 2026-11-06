#!/bin/sh
# Start the Telegram bot. Ollama must be running (`ollama serve`, or the Ollama app).
cd "$(dirname "$0")" || exit 1
PYTHONPATH=src exec venv/bin/python -m stockbot
