"""
Shared OpenCode utilities for all Trident agents.

This module provides common functions for OpenCode API interactions,
including model availability error detection, retry logic, and
event type conversion utilities.
"""

import json
from typing import List, Dict, Optional, Any


# Retry delays for model availability errors (in seconds)
# First attempt is immediate, then wait 1s, 5s, 10s before subsequent attempts
RETRY_DELAYS = [1, 5, 10]


class ModelAvailabilityError(Exception):
    """Raised when an OpenCode session fails due to model availability issues."""
    pass


def check_for_model_error(messages: Optional[List[Dict]]) -> Optional[str]:
    """Check if OpenCode session failed due to model availability error.

    Args:
        messages: List of OpenCode API message objects from /session/:id/message

    Returns:
        Error message if model availability error is found, None otherwise.

    Raises:
        ModelAvailabilityError: If a model availability error is detected.

    Example:
        >>> messages = [{"info": {"error": {"data": {"message": "model not found"}}}}]
        >>> check_for_model_error(messages)
        'model not found'
    """
    if not messages:
        return None

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        info = msg.get("info", {})
        if not isinstance(info, dict):
            continue
        error = info.get("error")
        if error and isinstance(error, dict):
            error_msg = error.get("data", {}).get("message", "")
            # Check for model availability errors
            if any(pattern in error_msg.lower() for pattern in [
                "model not found",
                "vllm engine is sleeping",
                "not found or vllm",
                "badrequesterror"
            ]):
                return error_msg
    return None


def raise_for_model_error(messages: Optional[List[Dict]]) -> None:
    """Raise ModelAvailabilityError if model availability error is detected.

    Args:
        messages: List of OpenCode API message objects

    Raises:
        ModelAvailabilityError: If a model availability error is detected.

    Example:
        >>> try:
        ...     raise_for_model_error(messages)
        ... except ModelAvailabilityError as e:
        ...     print(f"Model error: {e}")
    """
    error_msg = check_for_model_error(messages)
    if error_msg:
        raise ModelAvailabilityError(error_msg)


# ---------------------------------------------------------------------------
# Event type conversion utilities
# ---------------------------------------------------------------------------

# Mapping from OpenCode API part types to legacy event types
_PART_TYPE_MAP = {
    "step-start": "step_start",
    "step-finish": "step_finish",
    "tool": "tool_use",
    "text": "text",
}


def convert_part_type_to_legacy(part_type: str) -> Optional[str]:
    """Convert OpenCode API part type to legacy event type.

    OpenCode Server API uses hyphenated types (e.g., "step-start", "step-finish")
    while the legacy format uses underscores (e.g., "step_start", "step_finish").
    This function maps between the two formats.

    Args:
        part_type: The API part type string (e.g., "step-start", "tool", "text")

    Returns:
        The legacy event type string (e.g., "step_start", "tool_use", "text")
        or None if the part type is unknown.

    Example:
        >>> convert_part_type_to_legacy("step-start")
        'step_start'
        >>> convert_part_type_to_legacy("tool")
        'tool_use'
        >>> convert_part_type_to_legacy("unknown") is None
        True
    """
    return _PART_TYPE_MAP.get(part_type)


def convert_api_messages_to_legacy_jsonl(messages: List[Dict]) -> List[str]:
    """Convert OpenCode Server API message format to legacy JSONL format.

    Legacy format has one JSON event per line with top-level fields:
      {"type": "step_start|tool_use|text|step_finish",
       "timestamp": <ms_epoch>, "sessionID": ..., "part": {...}}

    API message format has messages containing parts:
      [{"info": {...}, "parts": [{"type": "step-start|tool|text|step-finish", ...}]}]

    Args:
        messages: List of OpenCode API message objects

    Returns:
        List of JSON strings, one per legacy event, in chronological order.

    Example:
        >>> messages = [{
        ...     "info": {"sessionID": "abc123", "time": {"created": 1234567890000}},
        ...     "parts": [{"type": "step-start", "time": {"start": 1234567890000}}]
        ... }]
        >>> lines = convert_api_messages_to_legacy_jsonl(messages)
        >>> len(lines)
        1
    """
    legacy_lines: List[str] = []

    for msg in messages:
        info = msg.get("info", {})
        parts = msg.get("parts", [])
        session_id = info.get("sessionID", "")

        for part in parts:
            part_type = part.get("type", "")

            # Convert API part type to legacy event type
            legacy_type = convert_part_type_to_legacy(part_type)
            if legacy_type is None:
                continue

            # Determine timestamp: use part time if available, else message time
            timestamp_ms = 0
            part_time = part.get("time", {})
            if part_time:
                timestamp_ms = part_time.get("start", 0) or part_time.get("end", 0)
            if not timestamp_ms:
                msg_time = info.get("time", {})
                timestamp_ms = msg_time.get("created", 0) or msg_time.get("completed", 0)

            # Build legacy event
            legacy_event: Dict = {
                "type": legacy_type,
                "timestamp": timestamp_ms,
                "sessionID": session_id,
                "part": part,
            }

            # For step_finish, add reason/cost/tokens from the part or message info
            if legacy_type == "step_finish":
                if "reason" not in part and info.get("finish"):
                    legacy_event["part"]["reason"] = info["finish"]
                if "cost" not in part and info.get("cost") is not None:
                    legacy_event["part"]["cost"] = info["cost"]
                if "tokens" not in part and info.get("tokens"):
                    legacy_event["part"]["tokens"] = info["tokens"]

            legacy_lines.append(json.dumps(legacy_event, separators=(",", ":")))

    return legacy_lines
