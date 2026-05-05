"""Shared JSONL session file parser.

Single source of truth for parsing conversation session files.
Used by both KollaborConversationLogger and ConversationManager.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def parse_session_jsonl(file_path: Path) -> Optional[Dict[str, Any]]:
    """Parse a JSONL session file and extract metadata.

    Returns a standardized dict with all session metadata fields.
    Both conversation_logger and conversation_manager delegate to this.

    Args:
        file_path: Path to the JSONL session file

    Returns:
        Session metadata dict or None on parse failure
    """
    try:
        session_info: Dict[str, Any] = {
            "session_id": file_path.stem,
            "file_path": str(file_path),
            "start_time": None,
            "end_time": None,
            "message_count": 0,
            "turn_count": 0,
            "topics": [],
            "working_directory": "unknown",
            "git_branch": "unknown",
            "last_activity": None,
            "size_bytes": file_path.stat().st_size,
            "duration": None,
            "preview_messages": [],
        }

        with open(file_path, "r") as f:
            lines = f.readlines()

        user_count = 0
        assistant_count = 0

        for line in lines:
            try:
                data = json.loads(line.strip())
            except json.JSONDecodeError:
                continue

            msg_type = data.get("type")

            if msg_type == "conversation_metadata":
                session_info["start_time"] = data.get("startTime")
                session_info["working_directory"] = data.get("cwd", "unknown")
                session_info["git_branch"] = data.get("gitBranch", "unknown")

            elif msg_type == "conversation_end":
                session_info["end_time"] = data.get("endTime")
                summary = data.get("summary", {})
                session_info["topics"] = summary.get("themes", [])

            elif msg_type == "user":
                user_count += 1
                session_info["message_count"] += 1

                if len(session_info["preview_messages"]) < 3:
                    content = data.get("message", {}).get("content", "")
                    preview = content[:100] + "..." if len(content) > 100 else content
                    session_info["preview_messages"].append(
                        {
                            "role": "user",
                            "content": preview,
                            "timestamp": data.get("timestamp"),
                        }
                    )

            elif msg_type == "assistant":
                assistant_count += 1
                session_info["message_count"] += 1

                if len(session_info["preview_messages"]) < 3:
                    content = data.get("message", {}).get("content", "")
                    if isinstance(content, list) and content:
                        content = content[0].get("text", "")
                    preview = content[:100] + "..." if len(content) > 100 else content
                    session_info["preview_messages"].append(
                        {
                            "role": "assistant",
                            "content": preview,
                            "timestamp": data.get("timestamp"),
                        }
                    )

        # Derived fields
        session_info["turn_count"] = user_count
        session_info["last_activity"] = (
            session_info["end_time"] or session_info["start_time"]
        )

        # Duration calculation
        if session_info["start_time"] and session_info["end_time"]:
            try:
                start = datetime.fromisoformat(
                    session_info["start_time"].replace("Z", "+00:00")
                )
                end = datetime.fromisoformat(
                    session_info["end_time"].replace("Z", "+00:00")
                )
                session_info["duration"] = f"{int((end - start).total_seconds() / 60)}m"
            except (ValueError, TypeError):
                session_info["duration"] = "unknown"

        return session_info

    except Exception as e:
        logger.warning(f"Failed to parse session file {file_path}: {e}")
        return None
