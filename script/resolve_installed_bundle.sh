#!/usr/bin/env bash
set -euo pipefail

# Development installs stay in the user's Applications folder. A dragged release
# in /Applications works without changing source paths or creating a LaunchAgent.
if [[ -d "$HOME/Applications/Jarvis.app" ]]; then
    echo "$HOME/Applications/Jarvis.app"
elif [[ -d /Applications/Jarvis.app ]]; then
    echo "/Applications/Jarvis.app"
else
    echo "$HOME/Applications/Jarvis.app"
fi
