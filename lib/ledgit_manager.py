"""
Ledgit Manager - Project-level file versioning with git.

Manages:
- Project identification and directory structure
- File syncing from source project (respecting .gitignore)
- Git operations (init, add, commit)
- Commit tracking (step_id -> commit SHA mapping)

Directory structure:
~/.claude/ledgit/projects/{project-hash}/
├── .git/                           # Git repo for files + trajectories
├── files/                          # Mirrored project files
├── trajectories/                   # Session trajectories
│   └── {session-folder}/
│       ├── trajectory.json
│       ├── trajectory.jsonl
│       ├── metadata.json
│       ├── state.json
│       └── commits.json            # step_id -> commit SHA mapping
├── ledgit.json                     # Project config
└── index.json                      # Sessions index for this project
"""

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Any


# Default ledgit directory
DEFAULT_LEDGIT_DIR = Path.home() / ".claude" / "ledgit"


@dataclass
class ProjectConfig:
    """Configuration for a ledgit project."""
    project_hash: str
    source_path: str
    project_name: str
    created_at: str
    remote_url: Optional[str] = None

    def to_dict(self) -> dict:
        result = {
            "project_hash": self.project_hash,
            "source_path": self.source_path,
            "project_name": self.project_name,
            "created_at": self.created_at,
        }
        if self.remote_url:
            result["remote_url"] = self.remote_url
        return result

    @classmethod
    def from_dict(cls, data: dict) -> "ProjectConfig":
        return cls(
            project_hash=data["project_hash"],
            source_path=data["source_path"],
            project_name=data["project_name"],
            created_at=data["created_at"],
            remote_url=data.get("remote_url"),
        )


@dataclass
class CommitRecord:
    """Record of a git commit linked to a trajectory step."""
    step_id: int
    event: str  # "before_user_message" or "after_agent_stop"
    commit_sha: str
    timestamp: str
    message: Optional[str] = None
    files_changed: int = 0

    def to_dict(self) -> dict:
        return {
            "step_id": self.step_id,
            "event": self.event,
            "commit_sha": self.commit_sha,
            "timestamp": self.timestamp,
            "message": self.message,
            "files_changed": self.files_changed,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CommitRecord":
        return cls(
            step_id=data["step_id"],
            event=data["event"],
            commit_sha=data["commit_sha"],
            timestamp=data["timestamp"],
            message=data.get("message"),
            files_changed=data.get("files_changed", 0),
        )


def get_ledgit_dir() -> Path:
    """Get the ledgit base directory."""
    env_dir = os.environ.get("LEDGIT_DIR")
    if env_dir:
        return Path(env_dir)
    return DEFAULT_LEDGIT_DIR


def compute_project_hash(project_path: str) -> str:
    """Compute a stable hash for a project path."""
    # Normalize the path
    canonical_path = str(Path(project_path).resolve())
    # Create hash (first 12 chars of SHA256)
    return hashlib.sha256(canonical_path.encode()).hexdigest()[:12]


def get_project_name(project_path: str) -> str:
    """Extract project name from path."""
    return Path(project_path).name or "unknown"


class GitIgnoreParser:
    """
    Parse and match gitignore patterns from source project.

    Collects patterns from:
    - Root .gitignore
    - Nested .gitignore files
    - Always ignores .git/ directories
    """

    def __init__(self, source_path: Path):
        self.source_path = source_path
        self.patterns: List[str] = []
        self._load_patterns()

    def _load_patterns(self) -> None:
        """Load all gitignore patterns from the source project."""
        # Always ignore .git directories
        self.patterns.append(".git/")
        self.patterns.append(".git")

        # Find and parse all .gitignore files
        for gitignore_path in self.source_path.rglob(".gitignore"):
            self._parse_gitignore(gitignore_path)

    def _parse_gitignore(self, gitignore_path: Path) -> None:
        """Parse a single gitignore file."""
        try:
            with open(gitignore_path, "r") as f:
                rel_dir = gitignore_path.parent.relative_to(self.source_path)
                for line in f:
                    line = line.strip()
                    # Skip empty lines and comments
                    if not line or line.startswith("#"):
                        continue
                    # Store pattern with relative directory context
                    if rel_dir == Path("."):
                        self.patterns.append(line)
                    else:
                        # Patterns in subdirectories apply to that subdirectory
                        self.patterns.append(f"{rel_dir}/{line}")
        except (IOError, OSError):
            pass

    def should_ignore(self, rel_path: Path) -> bool:
        """
        Check if a relative path should be ignored.

        Uses simple pattern matching. For production, consider using
        the `pathspec` or `gitignore-parser` library.
        """
        path_str = str(rel_path)
        path_parts = rel_path.parts

        for pattern in self.patterns:
            # Handle directory patterns (ending with /)
            if pattern.endswith("/"):
                dir_pattern = pattern.rstrip("/")
                if dir_pattern in path_parts:
                    return True
            # Handle exact matches
            elif pattern == path_str:
                return True
            # Handle wildcard patterns (simple implementation)
            elif "*" in pattern:
                import fnmatch
                if fnmatch.fnmatch(path_str, pattern):
                    return True
                # Also check against just the filename
                if fnmatch.fnmatch(rel_path.name, pattern):
                    return True
            # Handle patterns that match anywhere in path
            elif pattern in path_parts or path_str.endswith(f"/{pattern}"):
                return True
            # Handle patterns matching filename
            elif rel_path.name == pattern:
                return True

        return False


class LedgitManager:
    """
    Manager for ledgit project-level operations.

    Handles:
    - Project initialization and discovery
    - File syncing from source to ledgit repo
    - Git operations (commit, etc.)
    - Commit tracking per session
    """

    def __init__(self, source_path: str):
        """
        Initialize ledgit manager for a source project.

        Args:
            source_path: Path to the source project being tracked
        """
        self.source_path = Path(source_path).resolve()
        self.project_hash = compute_project_hash(str(self.source_path))
        self.project_name = get_project_name(str(self.source_path))

        self.ledgit_dir = get_ledgit_dir()
        self.project_dir = self.ledgit_dir / "projects" / self.project_hash
        self.files_dir = self.project_dir / "files"
        self.trajectories_dir = self.project_dir / "trajectories"

        self._config: Optional[ProjectConfig] = None
        self._ignore_parser: Optional[GitIgnoreParser] = None

    @property
    def config_file(self) -> Path:
        return self.project_dir / "ledgit.json"

    @property
    def project_index_file(self) -> Path:
        return self.project_dir / "index.json"

    @property
    def global_index_file(self) -> Path:
        return self.ledgit_dir / "index.json"

    @property
    def ignore_parser(self) -> GitIgnoreParser:
        if self._ignore_parser is None:
            self._ignore_parser = GitIgnoreParser(self.source_path)
        return self._ignore_parser

    def project_exists(self) -> bool:
        """Check if this project has been initialized."""
        return self.config_file.exists()

    def initialize_project(self) -> ProjectConfig:
        """
        Initialize a new ledgit project.

        Creates directory structure and initializes git repo.
        """
        # Create directories
        self.project_dir.mkdir(parents=True, exist_ok=True)
        self.files_dir.mkdir(exist_ok=True)
        self.trajectories_dir.mkdir(exist_ok=True)

        # Initialize git repo if not exists
        git_dir = self.project_dir / ".git"
        if not git_dir.exists():
            self._run_git(["init"], cwd=self.project_dir)

            # Create initial .gitignore for the ledgit repo
            gitignore_content = """# Ledgit repo gitignore
# Nothing to ignore - we want to track everything
"""
            (self.project_dir / ".gitignore").write_text(gitignore_content)

        # Create config
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        config = ProjectConfig(
            project_hash=self.project_hash,
            source_path=str(self.source_path),
            project_name=self.project_name,
            created_at=timestamp,
        )
        self._save_config(config)
        self._config = config

        # Update global index
        self._update_global_index()

        # Initial file sync and commit
        self.sync_files()
        self._run_git(["add", "."], cwd=self.project_dir)
        self._run_git(
            ["commit", "-m", "Initial ledgit snapshot", "--allow-empty"],
            cwd=self.project_dir
        )

        return config

    def load_config(self) -> Optional[ProjectConfig]:
        """Load project configuration."""
        if self._config is not None:
            return self._config

        if not self.config_file.exists():
            return None

        try:
            with open(self.config_file) as f:
                self._config = ProjectConfig.from_dict(json.load(f))
                return self._config
        except (json.JSONDecodeError, KeyError):
            return None

    def _save_config(self, config: ProjectConfig) -> None:
        """Save project configuration."""
        with open(self.config_file, "w") as f:
            json.dump(config.to_dict(), f, indent=2)

    def _update_global_index(self) -> None:
        """Update the global projects index."""
        self.ledgit_dir.mkdir(parents=True, exist_ok=True)

        # Load existing index
        index = {"projects": []}
        if self.global_index_file.exists():
            try:
                with open(self.global_index_file) as f:
                    index = json.load(f)
            except json.JSONDecodeError:
                pass

        # Remove existing entry for this project
        index["projects"] = [
            p for p in index.get("projects", [])
            if p.get("project_hash") != self.project_hash
        ]

        # Add current project
        config = self.load_config()
        if config:
            index["projects"].append({
                "project_hash": self.project_hash,
                "source_path": str(self.source_path),
                "project_name": self.project_name,
                "ledgit_path": str(self.project_dir),
            })

        with open(self.global_index_file, "w") as f:
            json.dump(index, f, indent=2)

    def sync_files(self) -> int:
        """
        Sync files from source project to ledgit files directory.

        Respects .gitignore patterns from source project.

        Returns:
            Number of files synced
        """
        files_synced = 0

        # Track which files exist in destination for cleanup
        existing_files = set()
        if self.files_dir.exists():
            for path in self.files_dir.rglob("*"):
                if path.is_file():
                    existing_files.add(path.relative_to(self.files_dir))

        # Walk source directory
        synced_files = set()
        for source_file in self.source_path.rglob("*"):
            if not source_file.is_file():
                continue

            # Get relative path
            rel_path = source_file.relative_to(self.source_path)

            # Check if should be ignored
            if self.ignore_parser.should_ignore(rel_path):
                continue

            # Determine destination
            dest_file = self.files_dir / rel_path
            synced_files.add(rel_path)

            # Check if file needs updating
            needs_copy = True
            if dest_file.exists():
                # Compare modification times and sizes
                src_stat = source_file.stat()
                dst_stat = dest_file.stat()
                if (src_stat.st_mtime <= dst_stat.st_mtime and
                    src_stat.st_size == dst_stat.st_size):
                    needs_copy = False

            if needs_copy:
                # Ensure parent directory exists
                dest_file.parent.mkdir(parents=True, exist_ok=True)
                # Copy file preserving metadata
                shutil.copy2(source_file, dest_file)
                files_synced += 1

        # Remove files that no longer exist in source
        for old_file in existing_files - synced_files:
            old_path = self.files_dir / old_file
            if old_path.exists():
                old_path.unlink()
                # Try to remove empty parent directories
                try:
                    old_path.parent.rmdir()
                except OSError:
                    pass

        return files_synced

    def _run_git(self, args: List[str], cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
        """Run a git command."""
        cwd = cwd or self.project_dir
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True
        )
        return result

    def create_snapshot(
        self,
        session_id: str,
        step_id: int,
        event: str,
        message: Optional[str] = None
    ) -> Optional[CommitRecord]:
        """
        Create a git snapshot of the current file state.

        Args:
            session_id: Current session ID
            step_id: Current trajectory step ID
            event: Event type ("before_user_message" or "after_agent_stop")
            message: Optional commit message

        Returns:
            CommitRecord with commit details, or None if no changes
        """
        # Ensure project is initialized
        if not self.project_exists():
            self.initialize_project()

        # Sync files from source
        files_synced = self.sync_files()

        # Stage all changes
        self._run_git(["add", "."], cwd=self.project_dir)

        # Check if there are changes to commit
        status = self._run_git(["status", "--porcelain"], cwd=self.project_dir)
        if not status.stdout.strip():
            # No changes, but we might still want to record the current commit
            head = self._run_git(["rev-parse", "HEAD"], cwd=self.project_dir)
            if head.returncode == 0:
                commit_sha = head.stdout.strip()
                timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                return CommitRecord(
                    step_id=step_id,
                    event=event,
                    commit_sha=commit_sha,
                    timestamp=timestamp,
                    message="No changes (referencing existing commit)",
                    files_changed=0,
                )
            return None

        # Count changed files
        changed_files = len([l for l in status.stdout.strip().split("\n") if l])

        # Create commit message
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if message is None:
            message = f"session:{session_id[:8]} step:{step_id} event:{event}"

        commit_msg = f"[ledgit] {message}\n\nSession: {session_id}\nStep: {step_id}\nEvent: {event}\nTimestamp: {timestamp}"

        # Commit
        result = self._run_git(["commit", "-m", commit_msg], cwd=self.project_dir)
        if result.returncode != 0:
            return None

        # Get commit SHA
        head = self._run_git(["rev-parse", "HEAD"], cwd=self.project_dir)
        commit_sha = head.stdout.strip()

        return CommitRecord(
            step_id=step_id,
            event=event,
            commit_sha=commit_sha,
            timestamp=timestamp,
            message=message,
            files_changed=changed_files,
        )

    def get_session_dir(self, session_folder_name: str) -> Path:
        """Get the directory for a session's trajectories."""
        return self.trajectories_dir / session_folder_name

    def save_commit_record(self, session_folder: str, record: CommitRecord) -> None:
        """Save a commit record to the session's commits.json."""
        session_dir = self.get_session_dir(session_folder)
        session_dir.mkdir(parents=True, exist_ok=True)

        commits_file = session_dir / "commits.json"

        # Load existing commits
        commits = {"snapshots": []}
        if commits_file.exists():
            try:
                with open(commits_file) as f:
                    commits = json.load(f)
            except json.JSONDecodeError:
                pass

        # Add new record
        commits["snapshots"].append(record.to_dict())

        # Save
        with open(commits_file, "w") as f:
            json.dump(commits, f, indent=2)

    def load_commit_records(self, session_folder: str) -> List[CommitRecord]:
        """Load all commit records for a session."""
        commits_file = self.get_session_dir(session_folder) / "commits.json"

        if not commits_file.exists():
            return []

        try:
            with open(commits_file) as f:
                data = json.load(f)
                return [CommitRecord.from_dict(r) for r in data.get("snapshots", [])]
        except (json.JSONDecodeError, KeyError):
            return []

    def set_remote(self, remote_url: str, remote_name: str = "origin") -> bool:
        """Set the git remote for this project."""
        # Check if remote exists
        result = self._run_git(["remote", "get-url", remote_name], cwd=self.project_dir)

        if result.returncode == 0:
            # Remote exists, update it
            self._run_git(["remote", "set-url", remote_name, remote_url], cwd=self.project_dir)
        else:
            # Add new remote
            self._run_git(["remote", "add", remote_name, remote_url], cwd=self.project_dir)

        # Update config
        config = self.load_config()
        if config:
            config.remote_url = remote_url
            self._save_config(config)

        return True

    def push(self, remote_name: str = "origin", branch: str = "main") -> bool:
        """Push to remote."""
        result = self._run_git(["push", "-u", remote_name, branch], cwd=self.project_dir)
        return result.returncode == 0
