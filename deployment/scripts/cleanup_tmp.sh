#!/usr/bin/env sh
set -eu

TEMP_ROOT="${TEMP_WORKSPACE_DIR:-/tmp/ai-video-studio}"
if [ -d "$TEMP_ROOT" ]; then
  # Remove scratch workspaces older than 120 minutes
  find "$TEMP_ROOT" -mindepth 1 -maxdepth 1 -mmin +120 -exec rm -rf {} +
  echo "Cleaned stale temp directories in $TEMP_ROOT"
else
  echo "No temp directory at $TEMP_ROOT"
fi

