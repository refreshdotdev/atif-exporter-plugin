#!/usr/bin/env python3
"""
SessionEnd Hook - Finalize ATIF trajectory and commit to ledgit.

This hook:
1. Assembles the JSONL trajectory into a final JSON file
2. Updates metadata with end time and stats
3. Creates a final git commit with all session data
4. Optionally pushes to remote
"""

import json
import os
import shutil
import sys
from pathlib import Path

# Add lib directory to path
SCRIPT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(SCRIPT_DIR / "lib"))

from atif_writer import ATIFWriter, FinalMetrics
from state_manager import StateManager, get_trajectories_dir


def copy_raw_transcript(transcript_path: str, session_dir: Path) -> None:
    """Copy the raw Claude Code transcript to the session directory."""
    if not transcript_path:
        return

    source = Path(transcript_path)
    if source.exists():
        dest = session_dir / "raw_transcript.jsonl"
        try:
            shutil.copy2(source, dest)
        except Exception:
            pass


def count_steps(jsonl_path: Path) -> int:
    """Count the number of steps in the trajectory JSONL."""
    count = 0
    if not jsonl_path.exists():
        return count

    try:
        with open(jsonl_path, "r") as f:
            for line in f:
                if not line.strip():
                    continue
                data = json.loads(line)
                if data.get("_type") == "step":
                    count += 1
    except Exception:
        pass

    return count


def main():
    """Handle SessionEnd event."""
    try:
        # Read hook input from stdin
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON input: {e}", file=sys.stderr)
        sys.exit(1)

    session_id = input_data.get("session_id", "unknown")
    transcript_path = input_data.get("transcript_path")
    reason = input_data.get("reason", "other")
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
    session_dir = state_manager.session_dir

    # Copy raw transcript
    copy_raw_transcript(transcript_path, session_dir)

    # Count steps
    jsonl_path = session_dir / "trajectory.jsonl"
    total_steps = count_steps(jsonl_path)

    # Finalize session metadata and update index
    metadata = state_manager.finalize_session(
        end_reason=reason,
        total_steps=total_steps
    )

    # Create final metrics
    final_metrics = FinalMetrics(
        total_prompt_tokens=state.total_prompt_tokens if state.total_prompt_tokens > 0 else None,
        total_completion_tokens=state.total_completion_tokens if state.total_completion_tokens > 0 else None,
        total_cached_tokens=state.total_cached_tokens if state.total_cached_tokens > 0 else None,
        total_cost_usd=state.total_cost_usd if state.total_cost_usd > 0 else None,
        total_steps=total_steps if total_steps > 0 else None,
        extra={
            "end_reason": reason,
            "ended_at": metadata.ended_at
        }
    )

    # Initialize ATIF writer and finalize
    writer = ATIFWriter(
        output_dir=trajectories_dir,
        session_id=session_id,
        agent_name="claude-code",
        agent_version="1.0.0",
        model_name=state.model_name
    )
    writer.session_dir = session_dir
    writer.jsonl_path = session_dir / "trajectory.jsonl"
    writer.json_path = session_dir / "trajectory.json"

    try:
        trajectory = writer.finalize(final_metrics)

        # Create final commit with session data in ledgit
        ledgit = state_manager.ledgit
        ledgit._run_git(["add", "."], cwd=ledgit.project_dir)
        ledgit._run_git(
            ["commit", "-m", f"[ledgit] Session {session_id[:8]} ended: {reason}"],
            cwd=ledgit.project_dir
        )

        print(f"Ledgit session complete: {ledgit.project_dir}", file=sys.stderr)
    except Exception as e:
        print(f"Error finalizing trajectory: {e}", file=sys.stderr)

    sys.exit(0)


if __name__ == "__main__":
    main()
