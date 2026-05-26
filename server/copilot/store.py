"""Disk-backed conversation + event log for the Ops Copilot.

Storage layout:

    .copilot/
        conversations/
            <conversation_id>.jsonl   # one JSON event per line

Each line is one structured event:
    {"ts": "...", "kind": "user_message" | "assistant_text" | "tool_use" |
                          "tool_result" | "proposal" | "approval" | "error",
     ... type-specific payload}

The agent loop and the chat endpoint both append here; the file is the source
of truth across server restarts.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


# Resolve once at import time so tests can monkey-patch via the env var.
BASE_DIR = Path(os.environ.get("COPILOT_STORE_DIR") or ".copilot")
CONV_DIR = BASE_DIR / "conversations"
CONV_DIR.mkdir(parents=True, exist_ok=True)


def _path(conversation_id: str) -> Path:
    # Defensive: forbid traversal even though IDs come from the server.
    safe = "".join(c for c in conversation_id if c.isalnum() or c in "-_")
    if not safe:
        raise ValueError("invalid conversation_id")
    return CONV_DIR / f"{safe}.jsonl"


def append_event(conversation_id: str, event: dict) -> None:
    """Atomically append one event to the conversation's JSONL file."""
    path = _path(conversation_id)
    event = {"ts": datetime.now(timezone.utc).isoformat(), **event}
    line = json.dumps(event, ensure_ascii=False, default=str) + "\n"
    # Open in append mode — single fd write of a single line is atomic on
    # POSIX up to PIPE_BUF (4096 bytes on Linux/mac). Long tool results can
    # exceed that, but a single FastAPI worker means there's no concurrent
    # writer anyway. Document the assumption so a future move to multi-worker
    # uvicorn forces a rethink (lock or fsync per write).
    with path.open("a", encoding="utf-8") as f:
        f.write(line)


def load_events(conversation_id: str) -> Iterator[dict]:
    """Stream events for a conversation in write order. Missing file → empty."""
    path = _path(conversation_id)
    if not path.exists():
        return iter(())

    def _gen():
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    # Corrupt line — skip rather than blow up the whole replay.
                    continue

    return _gen()


def build_message_history(conversation_id: str) -> list[dict]:
    """Reconstruct the `messages=[]` array Anthropic expects from the on-disk
    event log. Skips bookkeeping events (approval, error). Each assistant turn
    re-uses the *content blocks* from when it was emitted so tool_use IDs stay
    paired with their tool_result IDs."""
    msgs: list[dict] = []
    current_assistant_blocks: list[dict] | None = None

    for ev in load_events(conversation_id):
        kind = ev.get("kind")
        if kind == "user_message":
            # Close out any open assistant turn before appending the user one.
            if current_assistant_blocks is not None:
                msgs.append({"role": "assistant", "content": current_assistant_blocks})
                current_assistant_blocks = None
            msgs.append(
                {"role": "user", "content": [{"type": "text", "text": ev["text"]}]}
            )
        elif kind == "assistant_text":
            if current_assistant_blocks is None:
                current_assistant_blocks = []
            current_assistant_blocks.append({"type": "text", "text": ev["text"]})
        elif kind == "tool_use":
            if current_assistant_blocks is None:
                current_assistant_blocks = []
            current_assistant_blocks.append(
                {
                    "type": "tool_use",
                    "id": ev["tool_use_id"],
                    "name": ev["name"],
                    "input": ev["input"],
                }
            )
        elif kind == "tool_result":
            # Tool results go in a user turn. Flush the assistant turn first.
            if current_assistant_blocks is not None:
                msgs.append({"role": "assistant", "content": current_assistant_blocks})
                current_assistant_blocks = None
            msgs.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": ev["tool_use_id"],
                            "content": ev["content"],
                        }
                    ],
                }
            )

    if current_assistant_blocks is not None:
        msgs.append({"role": "assistant", "content": current_assistant_blocks})

    return msgs


def list_conversations() -> list[dict]:
    """List all conversations with last-event timestamp and event count."""
    out = []
    for p in sorted(CONV_DIR.glob("*.jsonl")):
        conv_id = p.stem
        events = list(load_events(conv_id))
        if not events:
            continue
        out.append(
            {
                "conversation_id": conv_id,
                "event_count": len(events),
                "last_ts": events[-1].get("ts"),
            }
        )
    return sorted(out, key=lambda x: x["last_ts"] or "", reverse=True)
