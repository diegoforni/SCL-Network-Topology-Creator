"""
Unified timeline entry writing utilities.
Ensures consistent timeline file handling across all agents.
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional, List
from datetime import datetime


def write_timeline_entry(
    dir: str,
    filename: str,
    entry: Dict[str, Any]
) -> None:
    """
    Write a timeline entry to a JSONL file.

    Uses append mode for safe concurrent writes.

    Args:
        dir: Directory path where the timeline file will be stored
        filename: Name of the timeline file (e.g., 'agent_timeline.jsonl')
        entry: Dictionary containing the timeline entry data
    """
    # Ensure directory exists
    Path(dir).mkdir(parents=True, exist_ok=True)

    # Full path to the timeline file
    filepath = os.path.join(dir, filename)

    # Append directly to file
    with open(filepath, 'a', encoding='utf-8') as f:
        json.dump(entry, f, separators=(',', ':'))
        f.write('\n')
        f.flush()


def read_timeline_entries(
    dir: str,
    filename: str,
    limit: Optional[int] = None
) -> List[Dict[str, Any]]:
    """
    Read timeline entries from a JSONL file.

    Args:
        dir: Directory path where the timeline file is stored
        filename: Name of the timeline file (e.g., 'agent_timeline.jsonl')
        limit: Optional maximum number of entries to read (most recent first)

    Returns:
        List of timeline entry dictionaries
    """
    filepath = os.path.join(dir, filename)

    if not os.path.exists(filepath):
        return []

    entries = []
    with open(filepath, 'r') as f:
        for line in f:
            if line.strip():
                entries.append(json.loads(line))

    if limit:
        # Return most recent entries
        entries = entries[-limit:]

    return entries


def get_timeline_path(dir: str, filename: str) -> str:
    """
    Get the full path to a timeline file.

    Args:
        dir: Directory path where the timeline file is stored
        filename: Name of the timeline file

    Returns:
        Full path to the timeline file
    """
    return os.path.join(dir, filename)
