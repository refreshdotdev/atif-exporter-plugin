#!/usr/bin/env python3
"""
Stop Hook - Capture final agent response and create file snapshot.

This hook:
1. Captures any final text response from the agent
2. Creates a git snapshot of the file state AFTER the agent finishes
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


def get_final_agent_response(transcript_path: str, last_position: int) -> tuple[str, str, int]:
    """
    Get the final agent response from the transcript.

    Args:
        transcript_path: Path to transcript file
        last_position: Last processed line number

    Returns:
        Tuple of (text_message, thinking_content, new_position)
    """
    try:
        parser = TranscriptParser(transcript_path)
        messages = parser.parse()

        text_parts = []
        thinking_parts = []
        new_position = last_position

        for i, message in enumerate(messages):
            if i < last_position:
                continue

            if message.role == "assistant":
                # Only capture if there are no tool calls (pure response)
                if not message.has_tool_calls:
                    if message.text:
                        text_parts.append(message.text)
                    if message.thinking:
                        thinking_parts.append(message.thinking)
                    new_position = i + 1

        text = "\n".join(text_parts) if text_parts else ""
        thinking = "\n".join(thinking_parts) if thinking_parts else ""

        return text, thinking, new_position
    except Exception:
        return "", "", last_position


def main():
    """Handle Stop event."""
    try:
        # Read hook input from stdin
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON input: {e}", file=sys.stderr)
        sys.exit(1)

    session_id = input_data.get("session_id", "unknown")
    transcript_path = input_data.get("transcript_path")
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

    # Get final response from transcript
    last_position = state_manager.get_transcript_position()
    text_message, thinking_content, new_position = get_final_agent_response(
        transcript_path, last_position
    )
    state_manager.update_transcript_position(new_position)

    # Get next step ID for this response
    step_id = state_manager.get_next_step_id()

    # Create snapshot AFTER agent finishes
    # This captures the file state after all the agent's changes
    snapshot_result = state_manager.create_snapshot(
        step_id=step_id,
        event="after_agent_stop",
        message=f"After agent response (step {step_id})"
    )

    # Only write a step if there's a final text response
    if text_message.strip():
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

        # Include snapshot info in step extra data
        extra = {
            "stop_hook_active": stop_hook_active,
            "cwd": cwd
        }
        if snapshot_result:
            extra["snapshot"] = {
                "event": "after_agent_stop",
                "commit_sha": snapshot_result.get("commit_sha"),
                "files_changed": snapshot_result.get("files_changed", 0)
            }

        # Write final agent step
        writer.write_agent_step(
            step_id=step_id,
            message=text_message,
            reasoning_content=thinking_content if thinking_content else None,
            model_name=state.model_name,
            extra=extra
        )

    # Allow the stop to proceed
    sys.exit(0)


if __name__ == "__main__":
    main()
