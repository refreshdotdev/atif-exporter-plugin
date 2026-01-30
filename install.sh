#!/bin/bash
set -e

PLUGIN_DIR="$HOME/.claude/plugins/atif-exporter-plugin"
SETTINGS_FILE="$HOME/.claude/settings.json"

echo "Installing ATIF Exporter plugin for Claude Code..."

# Create plugins directory
mkdir -p "$HOME/.claude/plugins"

# Clone or update the plugin
if [ -d "$PLUGIN_DIR" ]; then
    echo "Updating existing installation..."
    cd "$PLUGIN_DIR"
    git pull origin main
else
    echo "Cloning plugin..."
    git clone https://github.com/refreshdotdev/atif-exporter-plugin.git "$PLUGIN_DIR"
fi

# Add plugin to settings.json using Python (since it's required anyway)
python3 << EOF
import json
import os

settings_file = os.path.expanduser("~/.claude/settings.json")
plugin_path = os.path.expanduser("~/.claude/plugins/atif-exporter-plugin")

# Load existing settings or create new
settings = {}
if os.path.exists(settings_file):
    try:
        with open(settings_file, 'r') as f:
            settings = json.load(f)
    except (json.JSONDecodeError, IOError):
        settings = {}

# Ensure plugins array exists
if "plugins" not in settings:
    settings["plugins"] = []

# Add plugin if not already present
if plugin_path not in settings["plugins"]:
    settings["plugins"].append(plugin_path)

    # Write back
    os.makedirs(os.path.dirname(settings_file), exist_ok=True)
    with open(settings_file, 'w') as f:
        json.dump(settings, f, indent=2)
    print("Added plugin to ~/.claude/settings.json")
else:
    print("Plugin already in settings.json")
EOF

echo ""
echo "Installation complete!"
echo "Trajectories will be saved to: ~/.claude/ledgit/"
echo ""
echo "Run 'claude' from any directory to start capturing trajectories."
