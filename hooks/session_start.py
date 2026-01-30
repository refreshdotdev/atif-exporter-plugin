#!/usr/bin/env python3
"""
SessionStart Hook - Initialize ATIF trajectory and ledgit project.

This hook:
1. Initializes the ledgit project (if not exists) with git repo
2. Creates the session directory with proper naming:
   {timestamp}_{project-name}_{session-id}/
3. Initializes trajectory files and metadata
"""

import json
import os
import sys
from pathlib import Path

# Add lib directory to path
SCRIPT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(SCRIPT_DIR / "lib"))

from atif_writer import ATIFWriter
from state_manager import StateManager, get_trajectories_dir


def main():
    """Handle SessionStart event."""
    try:
        # Read hook input from stdin
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON input: {e}", file=sys.stderr)
        sys.exit(1)

    session_id = input_data.get("session_id", "unknown")
    model_name = input_data.get("model", None)
    source = input_data.get("source", "startup")  # startup, resume, clear, compact
    cwd = input_data.get("cwd", os.getcwd())

    # Get trajectories directory for this project (via ledgit)
    trajectories_dir = get_trajectories_dir(cwd)

    # Initialize state manager with project path
    state_manager = StateManager(
        trajectories_dir=trajectories_dir,
        session_id=session_id,
        project_path=cwd
    )

    # Ensure ledgit project is initialized (creates git repo, syncs files)
    state_manager.ensure_project_initialized()

    # Initialize session (creates folder, metadata, index entry)
    metadata = state_manager.initialize_session(model_name=model_name)

    # Store extra session info
    state_manager.set_extra("source", source)
    state_manager.set_extra("cwd", cwd)
    state_manager.set_extra("ledgit_project", state_manager.ledgit.project_hash)

    # Copy transcript path for reference
    transcript_path = input_data.get("transcript_path")
    if transcript_path:
        state_manager.set_extra("transcript_path", transcript_path)

    # Initialize ATIF writer and write header
    writer = ATIFWriter(
        output_dir=trajectories_dir,
        session_id=session_id,
        agent_name="claude-code",
        agent_version="1.0.0",
        model_name=model_name
    )
    # Point writer to the correct session directory
    writer.session_dir = state_manager.session_dir
    writer.jsonl_path = state_manager.session_dir / "trajectory.jsonl"
    writer.json_path = state_manager.session_dir / "trajectory.json"
    writer.write_header()

    # Output info (shown in verbose mode)
    ledgit_path = state_manager.ledgit.project_dir
    output = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": f"Ledgit project: {ledgit_path}, Session: {metadata.folder_name}"
        }
    }
    print(json.dumps(output))
    sys.exit(0)


if __name__ == "__main__":
    main()
