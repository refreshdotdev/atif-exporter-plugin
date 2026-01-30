# ATIF Exporter Plugin for Claude Code

Export Claude Code sessions to the Agent Trajectory Interchange Format (ATIF) for use with [Harbor](https://harbor.dev) and other trajectory analysis tools.

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/refreshdotdev/atif-exporter-plugin.git
cd atif-exporter-plugin
```

### 2. Add to Claude Code settings

Add the plugin to your global Claude settings (`~/.claude/settings.json`):

```json
{
  "plugins": ["/path/to/atif-exporter-plugin"]
}
```

Replace `/path/to/atif-exporter-plugin` with the actual path where you cloned the repo.

**That's it!** Now just run `claude` normally from any directory.

## How It Works

When you run Claude Code, the plugin automatically:

1. Creates a trajectory folder at `~/.claude/atif-trajectories/`
2. Names it with: `{timestamp}_{project-name}_{session-id}/`
3. Captures all interactions as ATIF-compliant steps
4. Maintains a global `index.json` for easy lookup

## Output Location

```
~/.claude/atif-trajectories/
├── index.json                                    # Global session index
├── 2025-01-29T10-30-00_my-project_abc12345/
│   ├── trajectory.json      # Complete ATIF trajectory
│   ├── trajectory.jsonl     # Incremental events (for live monitoring)
│   ├── metadata.json        # Session metadata
│   ├── state.json           # Internal state
│   └── raw_transcript.jsonl # Original transcript
└── 2025-01-29T11-45-00_other-repo_def67890/
    └── ...
```

## Custom Output Directory

Set the `ATIF_TRAJECTORIES_DIR` environment variable:

```bash
export ATIF_TRAJECTORIES_DIR=/custom/path/to/trajectories
claude
```

## Finding Your Trajectories

**By folder name**: Folders are named `{timestamp}_{project}_{session}` so you can easily find them by time or project.

**Watch live updates during a session**:
```bash
tail -f ~/.claude/atif-trajectories/*/trajectory.jsonl
```

**Using index.json**:
```bash
# View all sessions
cat ~/.claude/atif-trajectories/index.json | jq '.sessions'

# Find sessions for a specific project
cat ~/.claude/atif-trajectories/index.json | jq '.sessions[] | select(.project_name == "my-project")'
```

## Example Output

### trajectory.json
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
      "message": "Create a hello world file"
    },
    {
      "step_id": 2,
      "timestamp": "2025-01-29T10:30:02Z",
      "source": "agent",
      "reasoning_content": "The user wants a simple text file...",
      "message": "I'll create the file for you.",
      "tool_calls": [
        {
          "tool_call_id": "toolu_01ABC",
          "function_name": "Write",
          "arguments": {
            "file_path": "hello.txt",
            "content": "Hello, World!"
          }
        }
      ],
      "observation": {
        "results": [
          {
            "source_call_id": "toolu_01ABC",
            "content": "{\"success\": true}"
          }
        ]
      }
    }
  ],
  "final_metrics": {
    "total_steps": 2,
    "extra": {
      "end_reason": "prompt_input_exit",
      "ended_at": "2025-01-29T10:31:00Z"
    }
  }
}
```

## What Gets Captured

| Event | ATIF Step |
|-------|-----------|
| User sends message | `source: "user"` with message |
| Claude makes tool call | `source: "agent"` with thinking, tool_calls, observation |
| Claude responds (no tools) | `source: "agent"` with message |
| Subagent completes | `source: "system"` with summary |

## Hooks Reference

| Hook | Purpose |
|------|---------|
| `SessionStart` | Initialize trajectory, create folder, metadata, index entry |
| `UserPromptSubmit` | Capture user messages |
| `PostToolUse` | Capture tool calls and results |
| `Stop` | Capture final agent responses |
| `SessionEnd` | Finalize trajectory, update metadata and index |
| `SubagentStop` | Track subagent completions |

## Integration with Harbor

```python
from harbor.utils.trajectory_validator import validate_trajectory
import json

with open("~/.claude/atif-trajectories/2025-01-29T10-30-00_my-project_abc12345/trajectory.json") as f:
    trajectory = json.load(f)

is_valid = validate_trajectory(trajectory)
```

## Requirements

- Python 3.8+
- Claude Code CLI with plugin support

## License

MIT
