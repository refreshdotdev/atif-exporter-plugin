#!/usr/bin/env python3
"""
UserPromptSubmit Hook - Capture user messages and create file snapshot.

This hook:
1. Creates a git snapshot of the current file state (BEFORE agent acts)
2. Records the user's prompt as a step in the ATIF trajectory
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
    """Handle UserPromptSubmit event."""
    try:
        # Read hook input from stdin
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON input: {e}", file=sys.stderr)
        sys.exit(1)

    session_id = input_data.get("session_id", "unknown")
    prompt = input_data.get("prompt", "")
    cwd = input_data.get("cwd", os.getcwd())

    # Skip empty prompts
    if not prompt.strip():
        sys.exit(0)

    # Get trajectories directory for this project
    trajectories_dir = get_trajectories_dir(cwd)

    # Load state manager (will find existing session folder)
    state_manager = StateManager(
        trajectories_dir=trajectories_dir,
        session_id=session_id,
        project_path=cwd
    )
    state = state_manager.load_state()

    # Get next step ID
    step_id = state_manager.get_next_step_id()

    # Create snapshot BEFORE agent acts
    # This captures the file state at the moment the user submits their message
    snapshot_result = state_manager.create_snapshot(
        step_id=step_id,
        event="before_user_message",
        message=f"Before user message: {prompt[:50]}..." if len(prompt) > 50 else f"Before user message: {prompt}"
    )

    # Initialize ATIF writer pointing to correct session dir
    writer = ATIFWriter(
        output_dir=trajectories_dir,
        session_id=session_id,
        agent_name="claude-code",
        agent_version="1.0.0",
        model_name=state.model_name
    )
    writer.session_dir = state_manager.session_dir
    writer.jsonl_path = state_manager.session_dir / "trajectory.jsonl"
    writer.json_path = state_manager.session_dir / "trajectory.json"

    # Include snapshot info in step extra data
    extra = {
        "cwd": cwd,
        "permission_mode": input_data.get("permission_mode")
    }
    if snapshot_result:
        extra["snapshot"] = {
            "event": "before_user_message",
            "commit_sha": snapshot_result.get("commit_sha"),
            "files_changed": snapshot_result.get("files_changed", 0)
        }

    # Write user step
    writer.write_user_step(
        step_id=step_id,
        message=prompt,
        extra=extra
    )

    # Clear any pending tool calls from previous turn
    state_manager.clear_pending_tool_calls()

    # Exit successfully (don't block the prompt)
    sys.exit(0)


if __name__ == "__main__":
    main()
