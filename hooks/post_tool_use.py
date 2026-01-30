#!/usr/bin/env python3
"""
PostToolUse Hook - Capture tool calls and results for ATIF trajectory.

This hook fires after each tool execution, capturing:
- Tool name and arguments
- Tool result/observation
- Thinking content from transcript (when available)
"""

import json
import os
import sys
from pathlib import Path
from typing import Optional

# Add lib directory to path
SCRIPT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(SCRIPT_DIR / "lib"))

from atif_writer import ATIFWriter, ToolCall, Observation, ObservationResult
from state_manager import StateManager, get_trajectories_dir
from transcript_parser import TranscriptParser


def extract_thinking_for_tool_call(
    transcript_path: Optional[str],
    tool_use_id: str,
    last_position: int
) -> tuple[Optional[str], Optional[str], int]:
    """
    Extract thinking content and text message associated with a tool call.

    Args:
        transcript_path: Path to transcript file
        tool_use_id: ID of the tool call to find
        last_position: Last processed line number

    Returns:
        Tuple of (thinking_content, text_message, new_position)
    """
    if not transcript_path:
        return None, None, last_position

    try:
        parser = TranscriptParser(transcript_path)
        messages = parser.parse()

        for i, message in enumerate(messages):
            if i < last_position:
                continue

            if message.role == "assistant":
                for tool_use in message.tool_use_blocks:
                    if tool_use.id == tool_use_id:
                        thinking = message.thinking if message.thinking else None
                        text = message.text if message.text else None
                        return thinking, text, i + 1

        return None, None, last_position
    except Exception:
        return None, None, last_position


def serialize_tool_result(tool_response: any) -> str:
    """Convert tool response to string for ATIF observation."""
    if tool_response is None:
        return ""
    if isinstance(tool_response, str):
        return tool_response
    try:
        return json.dumps(tool_response, default=str)
    except (TypeError, ValueError):
        return str(tool_response)


def main():
    """Handle PostToolUse event."""
    try:
        # Read hook input from stdin
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON input: {e}", file=sys.stderr)
        sys.exit(1)

    session_id = input_data.get("session_id", "unknown")
    tool_name = input_data.get("tool_name", "unknown")
    tool_input = input_data.get("tool_input", {})
    tool_response = input_data.get("tool_response")
    tool_use_id = input_data.get("tool_use_id", "")
    transcript_path = input_data.get("transcript_path")
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

    # Extract thinking and text from transcript
    last_position = state_manager.get_transcript_position()
    thinking_content, text_message, new_position = extract_thinking_for_tool_call(
        transcript_path, tool_use_id, last_position
    )
    state_manager.update_transcript_position(new_position)

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

    # Create tool call
    tool_call = ToolCall(
        tool_call_id=tool_use_id or f"call_{step_id}",
        function_name=tool_name,
        arguments=tool_input
    )

    # Create observation with result
    result_content = serialize_tool_result(tool_response)
    is_error = False

    # Check if response indicates an error
    if isinstance(tool_response, dict):
        is_error = tool_response.get("error") is not None or tool_response.get("is_error", False)
    elif isinstance(tool_response, str) and tool_response.startswith("Error:"):
        is_error = True

    observation = Observation(
        results=[
            ObservationResult(
                source_call_id=tool_call.tool_call_id,
                content=result_content,
                is_error=is_error
            )
        ]
    )

    # Write agent step with tool call
    writer.write_agent_step(
        step_id=step_id,
        message=text_message,
        reasoning_content=thinking_content,
        tool_calls=[tool_call],
        observation=observation,
        model_name=state.model_name,
        extra={
            "tool_name": tool_name,
            "cwd": cwd
        }
    )

    # Exit successfully
    sys.exit(0)


if __name__ == "__main__":
    main()
