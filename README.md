# Ledgit - File Versioning for Claude Code

Export Claude Code sessions to the Agent Trajectory Interchange Format (ATIF) with **complete file versioning**. Every change the agent makes is tracked in a git repository, so you can see the exact state of your files at any point in the conversation.

## Features

- **ATIF Trajectory Export**: Captures all interactions in [Harbor](https://harborframework.com/)-compatible ATIF format
- **File Versioning (Ledgit)**: Git-based snapshots of your entire project at each conversation turn
- **Before/After Snapshots**: See exactly what changed when the agent acted
- **Per-Project Organization**: Each project has its own ledgit repo with all sessions
- **Respects .gitignore**: Only tracks files your project tracks

## Installation

Run this one-liner:

```bash
curl -fsSL https://raw.githubusercontent.com/refreshdotdev/ledgit/main/install.sh | bash
```

**That's it!** Now just run `claude` normally from any directory.

### Manual Installation

```bash
git clone https://github.com/refreshdotdev/ledgit.git ~/.claude/plugins/ledgit
```

Then add to `~/.claude/settings.json`:

```json
{
  "plugins": ["~/.claude/plugins/ledgit"]
}
```

## How It Works

When you run Claude Code, the plugin:

1. **Creates a ledgit project** at `~/.claude/ledgit/projects/{project-hash}/`
2. **Initializes a git repo** to track all files
3. **Snapshots files** before each user message and after each agent response
4. **Records trajectories** as ATIF-compliant steps
5. **Links snapshots to steps** so you can restore to any point

## Directory Structure

```
~/.claude/ledgit/
├── index.json                              # Global projects index
└── projects/
    └── {project-hash}/                     # One repo per source project
        ├── .git/                           # Git repo for versioning
        ├── files/                          # Mirrored project files
        ├── trajectories/                   # Session data
        │   └── {session-folder}/
        │       ├── trajectory.json         # Complete ATIF trajectory
        │       ├── trajectory.jsonl        # Incremental events
        │       ├── metadata.json           # Session metadata
        │       ├── commits.json            # step_id -> git commit mapping
        │       └── raw_transcript.jsonl    # Original transcript
        └── ledgit.json                     # Project config
```

## Snapshots: Before & After

Each conversation turn creates two snapshots:

1. **before_user_message**: Captures file state when you submit a message (before agent acts)
2. **after_agent_stop**: Captures file state after the agent finishes responding

This means you can always see:
- What the files looked like before the agent made changes
- Exactly what the agent changed
- The trajectory of the conversation that led to those changes

## Using the Snapshots

### View commit history
```bash
cd ~/.claude/ledgit/projects/{project-hash}
git log --oneline
```

### See what changed in a specific commit
```bash
git show {commit-sha}
```

### Restore files to a specific point
```bash
git checkout {commit-sha} -- files/
```

### Find snapshots for a session
```bash
cat trajectories/{session-folder}/commits.json | jq '.snapshots'
```

## Trajectory Format (ATIF v1.4)

```json
{
  "schema_version": "ATIF-v1.4",
  "session_id": "abc12345-full-uuid",
  "agent": {
    "name": "claude-code",
    "version": "1.0.0",
    "model_name": "claude-sonnet-4-20250514"
  },
  "steps": [
    {
      "step_id": 1,
      "timestamp": "2025-01-29T10:30:00Z",
      "source": "user",
      "message": "Create a hello world file",
      "extra": {
        "snapshot": {
          "event": "before_user_message",
          "commit_sha": "abc123",
          "files_changed": 0
        }
      }
    },
    {
      "step_id": 2,
      "timestamp": "2025-01-29T10:30:02Z",
      "source": "agent",
      "message": "I'll create the file for you.",
      "tool_calls": [...],
      "observation": {...},
      "extra": {
        "snapshot": {
          "event": "after_agent_stop",
          "commit_sha": "def456",
          "files_changed": 1
        }
      }
    }
  ]
}
```

## Custom Ledgit Location

```bash
export LEDGIT_DIR=/custom/path/to/ledgit
claude
```

## Setting Up a Remote

Each project can have its own git remote for backup:

```bash
cd ~/.claude/ledgit/projects/{project-hash}
git remote add origin git@github.com:you/project-ledgit.git
git push -u origin main
```

## What Gets Captured

| Event | ATIF Step | Snapshot |
|-------|-----------|----------|
| User sends message | `source: "user"` | before_user_message |
| Claude makes tool call | `source: "agent"` with tool_calls | - |
| Claude responds (no tools) | `source: "agent"` | after_agent_stop |
| Subagent completes | `source: "system"` | - |

## Hooks Reference

| Hook | Purpose |
|------|---------|
| `SessionStart` | Initialize ledgit project and session |
| `UserPromptSubmit` | Snapshot (before), capture user message |
| `PostToolUse` | Capture tool calls and results |
| `Stop` | Snapshot (after), capture final response |
| `SessionEnd` | Finalize trajectory, commit session data |
| `SubagentStop` | Track subagent completions |

## Gitignore Handling

The plugin respects your project's `.gitignore` files:

- Root `.gitignore` patterns are applied
- Nested `.gitignore` files are also respected
- `.git/` directories are always excluded
- Only tracked files are mirrored to ledgit

## Requirements

- Python 3.8+
- Git
- Claude Code CLI with plugin support

## License

MIT
