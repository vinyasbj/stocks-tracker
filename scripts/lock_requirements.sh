#!/bin/sh
# Regenerate requirements.txt from requirements.in with every package pinned, using the same
# Python 3.12 image the bot runs on (Docker on an Apple Silicon Mac is arm64 Linux, like the
# Oracle VM). Versions only, no hashes, so CI's x86-64 runner installs the same versions.
#
# Usage: scripts/lock_requirements.sh
set -eu
cd "$(dirname "$0")/.."
docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp \
    -e CUSTOM_COMPILE_COMMAND="scripts/lock_requirements.sh" \
    -v "$PWD":/work -w /work python:3.12-slim-bookworm sh -c '
        python -m venv /tmp/venv >/dev/null &&
        /tmp/venv/bin/pip install --quiet --disable-pip-version-check pip-tools &&
        /tmp/venv/bin/pip-compile --quiet --strip-extras --output-file requirements.txt requirements.in'
echo "requirements.txt regenerated"
