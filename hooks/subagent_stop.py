#!/usr/bin/env python3
"""
SubagentStop Hook - Handle subagent (Task tool) trajectory capture.

This hook fires when a subagent finishes, recording it as a system step.
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
from transcript_parser import TranscriptParser


def main():
    """Handle SubagentStop event."""
    try:
        # Read hook input from stdin
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON input: {e}", file=sys.stderr)
        sys.exit(1)

    session_id = input_data.get("session_id", "unknown")
    agent_id = input_data.get("agent_id", "unknown")
    agent_transcript_path = input_data.get("agent_transcript_path")
    stop_hook_active = input_data.get("stop_hook_active", False)
    cwd = input_data.get("cwd", os.getcwd())

    # Get trajectories directory for this project
    trajectories_dir = get_trajectories_dir(cwd)

    # Load state manager
    state_manager = StateManager(
        trajectories_dir=trajectories_dir,
        session_id=session_id,
        project_path=cwd
    )
    state = state_manager.load_state()

    # Parse subagent transcript to extract summary
    subagent_summary = None
    if agent_transcript_path:
        try:
            parser = TranscriptParser(agent_transcript_path)
            last_message = parser.get_last_assistant_message()
            if last_message and last_message.text:
                subagent_summary = last_message.text[:1000]
                if len(last_message.text) > 1000:
                    subagent_summary += "..."
        except Exception:
            pass

    # Get next step ID
    step_id = state_manager.get_next_step_id()

    # Initialize ATIF writer
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

    # Write a system step noting the subagent completion
    message = f"Subagent '{agent_id}' completed."
    if subagent_summary:
        message += f"\n\nSummary: {subagent_summary}"

    writer.write_system_step(
        step_id=step_id,
        message=message,
        extra={
            "agent_id": agent_id,
            "agent_transcript_path": agent_transcript_path,
            "stop_hook_active": stop_hook_active
        }
    )

    sys.exit(0)


if __name__ == "__main__":
    main()
