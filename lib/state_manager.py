"""
State Manager - Manage persistent state across hook invocations.

Since each hook runs in a separate process, state must be persisted to disk.
This module handles reading/writing session state for ATIF trajectory export.

Now integrated with Ledgit for project-level file versioning.

Directory structure:
~/.claude/ledgit/projects/{project-hash}/
├── .git/
├── files/
├── trajectories/
│   └── {session-folder}/     <- Session data lives here
│       ├── trajectory.json
│       ├── trajectory.jsonl
│       ├── metadata.json
│       ├── state.json
│       └── commits.json
├── ledgit.json
└── index.json

Session folder naming: {timestamp}_{project-name}_{session-id}/
Example: 2025-01-29T10-30-00_my-project_abc123/
"""

import json
import os
import re
import fcntl
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Any

# Import ledgit manager
try:
    from ledgit_manager import LedgitManager, get_ledgit_dir
except ImportError:
    from .ledgit_manager import LedgitManager, get_ledgit_dir


# Default ledgit directory (trajectories now live under projects)
DEFAULT_LEDGIT_DIR = Path.home() / ".claude" / "ledgit"


@dataclass
class SessionMetadata:
    """Metadata for a session, stored in metadata.json."""
    session_id: str
    folder_name: str
    project_path: str
    project_name: str
    started_at: str
    ended_at: Optional[str] = None
    model_name: Optional[str] = None
    total_steps: int = 0
    end_reason: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        result = {
            "session_id": self.session_id,
            "folder_name": self.folder_name,
            "project_path": self.project_path,
            "project_name": self.project_name,
            "started_at": self.started_at,
        }
        if self.ended_at:
            result["ended_at"] = self.ended_at
        if self.model_name:
            result["model_name"] = self.model_name
        if self.total_steps > 0:
            result["total_steps"] = self.total_steps
        if self.end_reason:
            result["end_reason"] = self.end_reason
        return result

    @classmethod
    def from_dict(cls, data: dict) -> "SessionMetadata":
        """Create from dictionary."""
        return cls(
            session_id=data["session_id"],
            folder_name=data["folder_name"],
            project_path=data["project_path"],
            project_name=data["project_name"],
            started_at=data["started_at"],
            ended_at=data.get("ended_at"),
            model_name=data.get("model_name"),
            total_steps=data.get("total_steps", 0),
            end_reason=data.get("end_reason"),
        )


@dataclass
class SessionState:
    """Persistent state for a Claude Code session."""
    session_id: str
    started_at: str
    folder_name: str = ""
    last_step_id: int = 0
    model_name: Optional[str] = None
    project_path: Optional[str] = None
    project_name: Optional[str] = None

    # Aggregate metrics
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_cached_tokens: int = 0
    total_cost_usd: float = 0.0

    # Tracking for parallel tool calls
    pending_tool_calls: dict[str, dict] = field(default_factory=dict)
    last_assistant_turn_id: Optional[int] = None

    # Track processed transcript lines to avoid duplicates
    last_transcript_line: int = 0

    # Extra metadata
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "session_id": self.session_id,
            "started_at": self.started_at,
            "folder_name": self.folder_name,
            "last_step_id": self.last_step_id,
            "model_name": self.model_name,
            "project_path": self.project_path,
            "project_name": self.project_name,
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_completion_tokens": self.total_completion_tokens,
            "total_cached_tokens": self.total_cached_tokens,
            "total_cost_usd": self.total_cost_usd,
            "pending_tool_calls": self.pending_tool_calls,
            "last_assistant_turn_id": self.last_assistant_turn_id,
            "last_transcript_line": self.last_transcript_line,
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SessionState":
        """Create from dictionary."""
        return cls(
            session_id=data["session_id"],
            started_at=data["started_at"],
            folder_name=data.get("folder_name", ""),
            last_step_id=data.get("last_step_id", 0),
            model_name=data.get("model_name"),
            project_path=data.get("project_path"),
            project_name=data.get("project_name"),
            total_prompt_tokens=data.get("total_prompt_tokens", 0),
            total_completion_tokens=data.get("total_completion_tokens", 0),
            total_cached_tokens=data.get("total_cached_tokens", 0),
            total_cost_usd=data.get("total_cost_usd", 0.0),
            pending_tool_calls=data.get("pending_tool_calls", {}),
            last_assistant_turn_id=data.get("last_assistant_turn_id"),
            last_transcript_line=data.get("last_transcript_line", 0),
            extra=data.get("extra", {}),
        )


def sanitize_folder_name(name: str) -> str:
    """Sanitize a string for use in folder names."""
    # Replace problematic characters with hyphens
    sanitized = re.sub(r'[^\w\-.]', '-', name)
    # Collapse multiple hyphens
    sanitized = re.sub(r'-+', '-', sanitized)
    # Remove leading/trailing hyphens
    sanitized = sanitized.strip('-')
    # Limit length
    return sanitized[:50] if sanitized else "unknown"


def generate_folder_name(session_id: str, project_path: str, timestamp: str) -> str:
    """
    Generate a folder name in format: {timestamp}_{project-name}_{session-id}

    Args:
        session_id: Claude Code session ID
        project_path: Path to the project directory
        timestamp: ISO timestamp string

    Returns:
        Sanitized folder name
    """
    # Extract project name from path
    project_name = Path(project_path).name if project_path else "unknown"
    project_name = sanitize_folder_name(project_name)

    # Format timestamp for folder name (replace colons with hyphens)
    folder_timestamp = timestamp.replace(":", "-").replace("T", "T")
    # Remove the Z and milliseconds for cleaner names
    folder_timestamp = folder_timestamp.split(".")[0].rstrip("Z")

    # Truncate session_id for readability (first 8 chars)
    short_session = session_id[:8] if session_id else "unknown"

    return f"{folder_timestamp}_{project_name}_{short_session}"


class StateManager:
    """
    Manager for session state persistence.

    Uses file locking to handle concurrent access from multiple hooks.
    Integrates with LedgitManager for project-level file versioning.
    """

    def __init__(self, trajectories_dir: Path | str, session_id: str,
                 project_path: Optional[str] = None):
        """
        Initialize the state manager.

        Args:
            trajectories_dir: Base directory for trajectory output (can be project-specific)
            session_id: Unique session identifier
            project_path: Path to the project being worked on
        """
        self.session_id = session_id
        self.project_path = project_path or os.getcwd()

        # Initialize ledgit manager for this project
        self._ledgit: Optional[LedgitManager] = None

        # Get the correct trajectories directory from ledgit
        if trajectories_dir:
            self.trajectories_dir = Path(trajectories_dir)
        else:
            self.trajectories_dir = get_trajectories_dir(self.project_path)

        self.trajectories_dir.mkdir(parents=True, exist_ok=True)

        # Try to find existing session folder, or we'll create one later
        self._session_dir: Optional[Path] = None
        self._state: Optional[SessionState] = None

        # Look for existing session
        existing_folder = self._find_session_folder()
        if existing_folder:
            self._session_dir = self.trajectories_dir / existing_folder

    @property
    def ledgit(self) -> LedgitManager:
        """Get the LedgitManager for this project."""
        if self._ledgit is None:
            self._ledgit = LedgitManager(self.project_path)
        return self._ledgit

    def ensure_project_initialized(self) -> None:
        """Ensure the ledgit project is initialized."""
        if not self.ledgit.project_exists():
            self.ledgit.initialize_project()

    def _find_session_folder(self) -> Optional[str]:
        """Find an existing folder for this session ID."""
        # Check index first
        index = self._load_index()
        for entry in index.get("sessions", []):
            if entry.get("session_id") == self.session_id:
                folder = entry.get("folder_name")
                if folder and (self.trajectories_dir / folder).exists():
                    return folder

        # Fallback: scan directories for matching session ID
        if self.trajectories_dir.exists():
            for folder in self.trajectories_dir.iterdir():
                if folder.is_dir() and self.session_id[:8] in folder.name:
                    state_file = folder / "state.json"
                    if state_file.exists():
                        try:
                            with open(state_file) as f:
                                data = json.load(f)
                                if data.get("session_id") == self.session_id:
                                    return folder.name
                        except (json.JSONDecodeError, KeyError):
                            continue
        return None

    @property
    def session_dir(self) -> Path:
        """Get the session directory, creating if needed."""
        if self._session_dir is None:
            # Create new folder with proper naming
            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            folder_name = generate_folder_name(
                self.session_id,
                self.project_path,
                timestamp
            )
            self._session_dir = self.trajectories_dir / folder_name
        return self._session_dir

    @property
    def state_file(self) -> Path:
        """Get path to state file."""
        return self.session_dir / "state.json"

    @property
    def metadata_file(self) -> Path:
        """Get path to metadata file."""
        return self.session_dir / "metadata.json"

    @property
    def index_file(self) -> Path:
        """Get path to global index file."""
        return self.trajectories_dir / "index.json"

    def ensure_session_dir(self) -> Path:
        """Ensure the session directory exists."""
        self.session_dir.mkdir(parents=True, exist_ok=True)
        return self.session_dir

    def _load_index(self) -> dict:
        """Load the global index file."""
        if self.index_file.exists():
            try:
                with open(self.index_file, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, KeyError):
                pass
        return {"sessions": []}

    def _save_index(self, index: dict) -> None:
        """Save the global index file."""
        self.trajectories_dir.mkdir(parents=True, exist_ok=True)
        temp_file = self.index_file.with_suffix(".tmp")
        try:
            with open(temp_file, "w") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    json.dump(index, f, indent=2)
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            temp_file.rename(self.index_file)
        except Exception:
            if temp_file.exists():
                temp_file.unlink()
            raise

    def add_to_index(self, metadata: SessionMetadata) -> None:
        """Add or update this session in the global index."""
        index = self._load_index()

        # Remove existing entry for this session if any
        index["sessions"] = [
            s for s in index["sessions"]
            if s.get("session_id") != self.session_id
        ]

        # Add new entry
        index["sessions"].append({
            "session_id": metadata.session_id,
            "folder_name": metadata.folder_name,
            "project_path": metadata.project_path,
            "project_name": metadata.project_name,
            "started_at": metadata.started_at,
            "ended_at": metadata.ended_at,
            "model_name": metadata.model_name,
        })

        # Sort by started_at descending (most recent first)
        index["sessions"].sort(
            key=lambda x: x.get("started_at", ""),
            reverse=True
        )

        self._save_index(index)

    def save_metadata(self, metadata: SessionMetadata) -> None:
        """Save session metadata to metadata.json."""
        self.ensure_session_dir()
        with open(self.metadata_file, "w") as f:
            json.dump(metadata.to_dict(), f, indent=2)

    def load_metadata(self) -> Optional[SessionMetadata]:
        """Load session metadata from metadata.json."""
        if self.metadata_file.exists():
            try:
                with open(self.metadata_file, "r") as f:
                    return SessionMetadata.from_dict(json.load(f))
            except (json.JSONDecodeError, KeyError):
                pass
        return None

    def load_state(self) -> SessionState:
        """
        Load session state from disk.

        Returns:
            SessionState object (creates new if doesn't exist)
        """
        if self._state is not None:
            return self._state

        if self.state_file.exists():
            try:
                with open(self.state_file, "r") as f:
                    fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                    try:
                        data = json.load(f)
                        self._state = SessionState.from_dict(data)
                    finally:
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except (json.JSONDecodeError, KeyError):
                self._state = self._create_new_state()
        else:
            self._state = self._create_new_state()

        return self._state

    def save_state(self, state: Optional[SessionState] = None) -> None:
        """
        Save session state to disk.

        Args:
            state: State to save (uses cached state if not provided)
        """
        if state is not None:
            self._state = state

        if self._state is None:
            return

        self.ensure_session_dir()

        # Write atomically with file locking
        temp_file = self.state_file.with_suffix(".tmp")
        try:
            with open(temp_file, "w") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    json.dump(self._state.to_dict(), f, indent=2)
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            temp_file.rename(self.state_file)
        except Exception:
            if temp_file.exists():
                temp_file.unlink()
            raise

    def _create_new_state(self) -> SessionState:
        """Create a new session state."""
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        project_name = Path(self.project_path).name if self.project_path else "unknown"

        return SessionState(
            session_id=self.session_id,
            started_at=timestamp,
            folder_name=self.session_dir.name,
            project_path=self.project_path,
            project_name=project_name,
        )

    def initialize_session(self, model_name: Optional[str] = None) -> SessionMetadata:
        """
        Initialize a new session with proper folder naming and metadata.

        Args:
            model_name: The model being used

        Returns:
            SessionMetadata for the new session
        """
        self.ensure_session_dir()

        state = self.load_state()
        state.folder_name = self.session_dir.name
        state.project_path = self.project_path
        state.project_name = Path(self.project_path).name if self.project_path else "unknown"
        if model_name:
            state.model_name = model_name
        self.save_state(state)

        # Create metadata
        metadata = SessionMetadata(
            session_id=self.session_id,
            folder_name=self.session_dir.name,
            project_path=self.project_path,
            project_name=state.project_name,
            started_at=state.started_at,
            model_name=model_name,
        )
        self.save_metadata(metadata)
        self.add_to_index(metadata)

        return metadata

    def finalize_session(self, end_reason: str, total_steps: int) -> SessionMetadata:
        """
        Finalize the session with end time and stats.

        Args:
            end_reason: Why the session ended
            total_steps: Total number of steps in trajectory

        Returns:
            Updated SessionMetadata
        """
        metadata = self.load_metadata()
        if metadata is None:
            state = self.load_state()
            metadata = SessionMetadata(
                session_id=self.session_id,
                folder_name=self.session_dir.name,
                project_path=state.project_path or self.project_path,
                project_name=state.project_name or "unknown",
                started_at=state.started_at,
                model_name=state.model_name,
            )

        metadata.ended_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        metadata.end_reason = end_reason
        metadata.total_steps = total_steps

        self.save_metadata(metadata)
        self.add_to_index(metadata)

        return metadata

    def create_snapshot(self, step_id: int, event: str, message: Optional[str] = None) -> Optional[dict]:
        """
        Create a git snapshot of the current file state.

        Args:
            step_id: Current trajectory step ID
            event: Event type ("before_user_message" or "after_agent_stop")
            message: Optional commit message

        Returns:
            CommitRecord dict with commit details, or None if failed
        """
        try:
            # Ensure project is initialized
            self.ensure_project_initialized()

            # Create snapshot via ledgit
            record = self.ledgit.create_snapshot(
                session_id=self.session_id,
                step_id=step_id,
                event=event,
                message=message
            )

            if record:
                # Save commit record to session
                self.ledgit.save_commit_record(self.session_dir.name, record)
                return record.to_dict()

            return None
        except Exception as e:
            # Log error but don't fail the hook
            import sys
            print(f"Warning: Failed to create snapshot: {e}", file=sys.stderr)
            return None

    def get_next_step_id(self) -> int:
        """Get and increment the next step ID."""
        state = self.load_state()
        state.last_step_id += 1
        self.save_state(state)
        return state.last_step_id

    def update_metrics(
        self,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cached_tokens: int = 0,
        cost_usd: float = 0.0
    ) -> None:
        """Update aggregate metrics."""
        state = self.load_state()
        state.total_prompt_tokens += prompt_tokens
        state.total_completion_tokens += completion_tokens
        state.total_cached_tokens += cached_tokens
        state.total_cost_usd += cost_usd
        self.save_state(state)

    def set_model_name(self, model_name: str) -> None:
        """Set the model name for the session."""
        state = self.load_state()
        state.model_name = model_name
        self.save_state(state)

    def add_pending_tool_call(self, tool_call_id: str, tool_data: dict) -> None:
        """Track a pending tool call."""
        state = self.load_state()
        state.pending_tool_calls[tool_call_id] = tool_data
        self.save_state(state)

    def get_pending_tool_calls(self) -> dict[str, dict]:
        """Get all pending tool calls."""
        state = self.load_state()
        return state.pending_tool_calls.copy()

    def clear_pending_tool_calls(self) -> dict[str, dict]:
        """Clear and return all pending tool calls."""
        state = self.load_state()
        pending = state.pending_tool_calls.copy()
        state.pending_tool_calls = {}
        self.save_state(state)
        return pending

    def set_last_assistant_turn_id(self, turn_id: int) -> None:
        """Track the last assistant turn."""
        state = self.load_state()
        state.last_assistant_turn_id = turn_id
        self.save_state(state)

    def get_last_assistant_turn_id(self) -> Optional[int]:
        """Get the last assistant turn ID."""
        state = self.load_state()
        return state.last_assistant_turn_id

    def update_transcript_position(self, line_number: int) -> None:
        """Track transcript processing position."""
        state = self.load_state()
        state.last_transcript_line = line_number
        self.save_state(state)

    def get_transcript_position(self) -> int:
        """Get the last processed transcript line."""
        state = self.load_state()
        return state.last_transcript_line

    def set_extra(self, key: str, value: Any) -> None:
        """Set an extra metadata value."""
        state = self.load_state()
        state.extra[key] = value
        self.save_state(state)

    def get_extra(self, key: str, default: Any = None) -> Any:
        """Get an extra metadata value."""
        state = self.load_state()
        return state.extra.get(key, default)


def get_trajectories_dir(project_path: Optional[str] = None) -> Path:
    """
    Get the trajectories output directory.

    With ledgit integration, trajectories are stored per-project under:
    ~/.claude/ledgit/projects/{project-hash}/trajectories/

    Args:
        project_path: Path to the source project. If provided, returns the
                     project-specific trajectories directory.

    Returns:
        Path to trajectories directory
    """
    # Check environment variable for custom ledgit location
    ledgit_dir = get_ledgit_dir()

    if project_path:
        # Return project-specific trajectories directory
        ledgit = LedgitManager(project_path)
        return ledgit.trajectories_dir

    # Return global ledgit directory (for index lookups, etc.)
    return ledgit_dir


def get_ledgit_manager(project_path: str) -> LedgitManager:
    """
    Get a LedgitManager for the given project.

    Args:
        project_path: Path to the source project

    Returns:
        LedgitManager instance
    """
    return LedgitManager(project_path)
