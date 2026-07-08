"""
OpenCode Client Base Class

This module provides a shared base class for OpenCode Server API clients.
It extracts common functionality for interacting with OpenCode servers via
the HTTP REST API.

All Trident agents that interact with OpenCode should inherit from this class
to ensure consistent behavior and reduce code duplication.

Example:
    class MyAgent(OpenCodeClient):
        def __init__(self):
            super().__init__(host="127.0.0.1", port=4096, agent="my_agent")
"""

import os
import sys
import time
import json
import signal
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from uuid import uuid4

import requests

# Import shared constants and utilities
# Try to import from shared package first (Docker environment)
try:
    from shared.constants import GRACE_PERIOD_SECONDS
    from shared.opencode_utils import (
        RETRY_DELAYS, ModelAvailabilityError, check_for_model_error,
        convert_api_messages_to_legacy_jsonl as _convert_api_messages_to_legacy_jsonl
    )
    from shared.timeline import write_timeline_entry
except ImportError:
    # Fallback for direct imports (development)
    from constants import GRACE_PERIOD_SECONDS
    from opencode_utils import (
        RETRY_DELAYS, ModelAvailabilityError, check_for_model_error,
        convert_api_messages_to_legacy_jsonl as _convert_api_messages_to_legacy_jsonl
    )
    from timeline import write_timeline_entry


class OpenCodeClient:
    """Base class for OpenCode Server API clients.

    Provides common methods for:
    - Server health checks and waiting for server availability
    - Session management (create, abort, status query, message retrieval)
    - Sending messages (async and sync modes)
    - Session completion polling
    - Log saving and format conversion

    Uses instance-level state for thread safety and to avoid global variables.
    """

    # Default configuration
    DEFAULT_SERVER_HOST = "127.0.0.1"
    DEFAULT_SERVER_PORT = 4096
    DEFAULT_TIMEOUT = 600
    DEFAULT_STATUS_POLL_INTERVAL = 3.0

    # Busy and idle state patterns
    _BUSY_STATES = ("busy", "pending", "running", "active", "generating")
    _IDLE_STATES = ("completed", "idle", "ready", "done")

    # Context overflow error phrases
    _CONTEXT_OVERFLOW_PHRASES = (
        "requested token count exceeds",
        "context length of",
        "exceeds the model's maximum context",
        "maximum context length",
        "input messages or the completion to fit within the limit",
    )

    def __init__(
        self,
        host: str = None,
        port: int = None,
        agent: str = "default",
        timeout: int = None,
        status_poll_interval: float = None
    ):
        """Initialize the OpenCode client.

        Args:
            host: OpenCode server host (default: from env or DEFAULT_SERVER_HOST)
            port: OpenCode server port (default: from env or DEFAULT_SERVER_PORT)
            agent: Agent name to use for sessions
            timeout: Default timeout for operations (default: DEFAULT_TIMEOUT)
            status_poll_interval: Poll interval for status checks (default: DEFAULT_STATUS_POLL_INTERVAL)
        """
        self.host = host or os.getenv("OPENCODE_SERVER_HOST", self.DEFAULT_SERVER_HOST)
        self.port = port or int(os.getenv("OPENCODE_SERVER_PORT", str(self.DEFAULT_SERVER_PORT)))
        self.agent = agent
        self.timeout = timeout or self.DEFAULT_TIMEOUT
        self.status_poll_interval = status_poll_interval or self.DEFAULT_STATUS_POLL_INTERVAL

        # Instance-level state tracking
        self._active_session_id: Optional[str] = None
        self._last_wait_error: Optional[str] = None
        self._last_logged_status: Optional[str] = None
        self._written_timeline_events: set = set()  # Track written timeline events to avoid duplicates

    def get_base_url(self) -> str:
        """Get the OpenCode server base URL for this client."""
        return f"http://{self.host}:{self.port}"

    def check_health(self) -> bool:
        """Check if the OpenCode server is healthy.

        Returns:
            True if the server responds with healthy=True, False otherwise.
        """
        base_url = self.get_base_url()
        try:
            resp = requests.get(f"{base_url}/global/health", timeout=5)
            if resp.status_code == 200:
                return resp.json().get("healthy", False)
        except Exception:
            pass
        return False

    def wait_for_server(self, timeout: int = 120) -> bool:
        """Wait for the OpenCode server to become available.

        Args:
            timeout: Maximum time to wait in seconds (default: 120)

        Returns:
            True if server becomes healthy within timeout, False otherwise.
        """
        start = time.time()
        while time.time() - start < timeout:
            if self.check_health():
                return True
            time.sleep(2)
        return False

    def create_session(self, title: Optional[str] = None) -> Optional[str]:
        """Create a new OpenCode session.

        Args:
            title: Optional session title

        Returns:
            Session ID if successful, None otherwise.
        """
        base_url = self.get_base_url()
        try:
            body: dict = {}
            if title:
                body["title"] = title
            resp = requests.post(f"{base_url}/session", json=body, timeout=60)
            resp.raise_for_status()
            return resp.json().get("id")
        except Exception as exc:
            print(f"[{self.agent}] Failed to create session: {exc}", file=sys.stderr)
            return None

    def send_message_async(
        self, session_id: str, message: str, agent: str = None
    ) -> bool:
        """Send a message asynchronously (fire-and-forget).

        Args:
            session_id: Target session ID
            message: Message text to send
            agent: Agent name (default: self.agent)

        Returns:
            True if message was accepted, False otherwise.
        """
        base_url = self.get_base_url()
        try:
            body = {
                "parts": [{"type": "text", "text": message}],
                "agent": agent or self.agent,
            }
            resp = requests.post(
                f"{base_url}/session/{session_id}/prompt_async",
                json=body,
                timeout=30,
            )
            return resp.status_code in (200, 204)
        except Exception as exc:
            print(f"[{self.agent}] Failed to send async message: {exc}",
                  file=sys.stderr)
            return False

    def send_message_sync(
        self, session_id: str, message: str, agent: str = None,
        timeout: int = None
    ) -> Optional[Dict]:
        """Send a message synchronously and wait for the response.

        Args:
            session_id: Target session ID
            message: Message text to send
            agent: Agent name (default: self.agent)
            timeout: Request timeout (default: self.timeout)

        Returns:
            Response JSON if successful, None otherwise.
        """
        base_url = self.get_base_url()
        timeout = timeout or self.timeout
        try:
            body = {
                "parts": [{"type": "text", "text": message}],
                "agent": agent or self.agent,
            }
            resp = requests.post(
                f"{base_url}/session/{session_id}/message",
                json=body,
                timeout=timeout,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            print(f"[{self.agent}] Failed to send sync message: {exc}",
                  file=sys.stderr)
            return None

    def get_session_status(
        self, session_id: Optional[str] = None
    ) -> Optional[Dict]:
        """Get session status from the server.

        Args:
            session_id: Specific session ID to query, or None for all sessions

        Returns:
            Status dict for the session, or dict of all sessions if session_id is None.
            Returns None on error.
        """
        base_url = self.get_base_url()
        try:
            resp = requests.get(f"{base_url}/session/status", timeout=10)
            resp.raise_for_status()
            all_statuses = resp.json()
            if session_id:
                return all_statuses.get(session_id)
            return all_statuses
        except Exception as exc:
            print(f"[{self.agent}] Failed to get session status: {exc}",
                  file=sys.stderr)
            return None

    def get_session_messages(self, session_id: str) -> Optional[List]:
        """Fetch all messages from a session (with retries).

        Args:
            session_id: Session ID to fetch messages from

        Returns:
            List of message objects, or None on failure.
        """
        base_url = self.get_base_url()
        for attempt in range(3):
            try:
                resp = requests.get(
                    f"{base_url}/session/{session_id}/message", timeout=30)
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:
                print(f"[{self.agent}] Attempt {attempt + 1}/3 get messages "
                      f"failed: {exc}", file=sys.stderr)
                time.sleep(2)
        return None

    def abort_session(self, session_id: str) -> bool:
        """Abort a running session.

        Args:
            session_id: Session ID to abort

        Returns:
            True if abort was successful, False otherwise.
        """
        base_url = self.get_base_url()
        try:
            resp = requests.post(
                f"{base_url}/session/{session_id}/abort", timeout=10)
            return resp.status_code == 200
        except Exception:
            return False

    def summarize_session(
        self, session_id: str,
        provider_id: str = "e-infra-chat",
        model_id: str = "gemma4"
    ) -> bool:
        """Ask the OpenCode server to compress a session's history in-place.

        Uses POST /session/:id/summarize. After a successful call the
        session's token count drops significantly.

        Args:
            session_id: Session ID to summarize
            provider_id: Provider ID for summarization
            model_id: Model ID for summarization

        Returns:
            True if successful, False otherwise.
        """
        base_url = self.get_base_url()
        try:
            body = {"providerID": provider_id, "modelID": model_id}
            resp = requests.post(
                f"{base_url}/session/{session_id}/summarize",
                json=body,
                timeout=120,
            )
            if resp.status_code == 200:
                result = resp.json()
                return bool(result) if not isinstance(result, bool) else result
            print(f"[{self.agent}] summarize_session HTTP {resp.status_code}: "
                  f"{resp.text[:200]}", file=sys.stderr)
            return False
        except Exception as exc:
            print(f"[{self.agent}] Failed to summarize session: {exc}",
                  file=sys.stderr)
            return False

    def fork_session(
        self, session_id: str, message_id: Optional[str] = None
    ) -> Optional[str]:
        """Fork an existing session to continue with full context.

        Args:
            session_id: Session ID to fork
            message_id: Optional message ID to fork from

        Returns:
            New session ID if successful, None otherwise.
        """
        base_url = self.get_base_url()
        try:
            body: dict = {}
            if message_id:
                body["messageID"] = message_id
            resp = requests.post(
                f"{base_url}/session/{session_id}/fork",
                json=body, timeout=60)
            resp.raise_for_status()
            return resp.json().get("id")
        except Exception as exc:
            print(f"[{self.agent}] Failed to fork session: {exc}", file=sys.stderr)
            return None

    def is_context_overflow(self, status_str: str) -> bool:
        """Check if a status string indicates context window overflow.

        Args:
            status_str: Status string to check

        Returns:
            True if status indicates context overflow, False otherwise.
        """
        s = status_str.lower()
        return any(phrase in s for phrase in self._CONTEXT_OVERFLOW_PHRASES)

    def wait_for_session_complete(
        self, session_id: str, timeout: int = None
    ) -> bool:
        """Poll session status until it completes, fails, or times out.

        The function waits through an initial grace period during which
        "idle" / "ready" statuses are NOT treated as completion. This
        avoids the race where polling starts before the server picks up
        the async prompt.

        Args:
            session_id: Session ID to monitor
            timeout: Maximum time to wait (default: self.timeout)

        Returns:
            True if session completed (or errored), False if timed out.
        """
        timeout = timeout or self.timeout
        self._last_wait_error = None
        start = time.time()
        saw_busy = False
        self._last_logged_status = None

        while True:
            elapsed = time.time() - start
            if elapsed >= timeout:
                break

            status = self.get_session_status(session_id)

            # Session disappeared from status map
            if status is None:
                if self._last_logged_status != "__none__":
                    print(f"[{self.agent}]   status: None (saw_busy={saw_busy}, "
                          f"elapsed={elapsed:.0f}s)")
                    self._last_logged_status = "__none__"
                if saw_busy or elapsed > GRACE_PERIOD_SECONDS:
                    print(f"[{self.agent}] Session {session_id[:12]} completed "
                          f"({elapsed:.0f}s)")
                    return True
                time.sleep(self.status_poll_interval)
                continue

            status_str = str(status).lower()
            if status_str != self._last_logged_status:
                print(f"[{self.agent}]   status: {status_str[:80]} "
                      f"(saw_busy={saw_busy}, elapsed={elapsed:.0f}s)")
                self._last_logged_status = status_str

            # Track whether we ever saw the session actively working
            if any(s in status_str for s in self._BUSY_STATES):
                saw_busy = True

            # Hard errors are always final
            if "error" in status_str or "failed" in status_str:
                self._last_wait_error = status_str
                print(f"[{self.agent}] Session {session_id[:12]} errored: "
                      f"{status_str}", file=sys.stderr)
                return True

            # Idle / completed states
            if any(s in status_str for s in self._IDLE_STATES):
                if saw_busy:
                    return True
                if elapsed > GRACE_PERIOD_SECONDS:
                    print(f"[{self.agent}] Session {session_id[:12]}: still "
                          f"idle after grace period ({elapsed:.0f}s), "
                          f"treating as completed")
                    return True

            time.sleep(self.status_poll_interval)

        print(f"[{self.agent}] Session {session_id[:12]} timed out after "
              f"{timeout}s", file=sys.stderr)
        return False

    def convert_api_messages_to_legacy_jsonl(self, messages: List[Dict]) -> List[str]:
        """Convert OpenCode Server API message format to legacy JSONL format.

        Delegates to the shared utility function in opencode_utils.

        Args:
            messages: List of API message objects

        Returns:
            List of JSONL strings (one per event).
        """
        return _convert_api_messages_to_legacy_jsonl(messages)

    def save_session_logs(
        self, session_id: str, output_dir: str,
        timeline_path: str = None, execution_id: str = None,
        session_num: int = 1, log_prefix: str = None
    ) -> Dict:
        """Fetch session messages and save in both API and legacy JSONL formats.

        Saves:
          - opencode_api_messages.json  : Full API response (JSON array)
          - opencode_stdout.jsonl       : Legacy JSONL format (one event per line)

        Also writes each OpenCode event to the timeline if provided.

        Args:
            session_id: Session ID to fetch logs from
            output_dir: Directory to save log files
            timeline_path: Optional timeline file path
            execution_id: Optional execution ID for tracking
            session_num: Session number for labeling
            log_prefix: Prefix for log messages (default: self.agent)

        Returns:
            Dict with parsed metrics (final_output, llm_calls, tool_calls, etc.).
        """
        log_prefix = log_prefix or self.agent
        api_path = os.path.join(output_dir, "opencode_api_messages.json")
        legacy_path = os.path.join(output_dir, "opencode_stdout.jsonl")

        messages = self.get_session_messages(session_id)
        if messages is None:
            print(f"[{log_prefix}] No messages retrieved for session "
                  f"{session_id[:12]}", file=sys.stderr)
            return {"final_output": None, "llm_calls": 0,
                    "tool_calls": [], "errors": [],
                    "messages": None}

        if not messages:
            print(f"[{log_prefix}] Empty message list for session "
                  f"{session_id[:12]}")
            return {"final_output": None, "llm_calls": 0,
                    "tool_calls": [], "errors": [],
                    "messages": messages}

        # Save API format (load existing, append, rewrite)
        existing: List = []
        if os.path.exists(api_path):
            try:
                with open(api_path, "r", encoding="utf-8") as fh:
                    existing = json.load(fh)
            except (json.JSONDecodeError, OSError):
                existing = []

        existing.append({
            "session_id": session_id,
            "session_num": session_num,
            "exec": execution_id[:8] if execution_id else None,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "messages": messages,
        })
        with open(api_path, "w", encoding="utf-8") as fh:
            json.dump(existing, fh, indent=2)
        print(f"[{log_prefix}] Saved API messages → {api_path}")

        # Convert and save legacy JSONL format (append)
        legacy_lines = self.convert_api_messages_to_legacy_jsonl(messages)
        with open(legacy_path, "a", encoding="utf-8") as fh:
            for line in legacy_lines:
                fh.write(line + "\n")
        print(f"[{log_prefix}] Saved legacy JSONL ({len(legacy_lines)} events) → "
              f"{legacy_path}")

        # Write each OpenCode event to the timeline (deduplicated)
        if timeline_path:
            for line in legacy_lines:
                try:
                    event = json.loads(line)
                    event_type = event.get("type", "")

                    # Create unique identifier for this event
                    event_id = event.get("id", "")
                    if not event_id and event_type == "tool":
                        event_id = event.get("state", {}).get("callID", "")
                    if not event_id:
                        event_id = f"{event_type}_{event.get('timestamp', '')}"

                    # Skip if already written
                    unique_key = f"{session_id}:{event_id}"
                    if unique_key in self._written_timeline_events:
                        continue

                    entry = {
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "level": "OPENCODE",
                        "msg": event_type,
                        "exec": execution_id[:8] if execution_id else None,
                        "data": event,
                    }
                    with open(timeline_path, "a", encoding="utf-8") as handle:
                        handle.write(
                            json.dumps(entry, separators=(",", ":")) + "\n"
                        )
                    self._written_timeline_events.add(unique_key)
                except (json.JSONDecodeError, ValueError):
                    continue

        # Parse metrics
        llm_calls = 0
        tool_calls: List[str] = []
        text_outputs: List[str] = []
        errors: List[str] = []
        total_tokens = {"input": 0, "output": 0, "reasoning": 0}
        total_cost = 0.0

        for msg in messages:
            info = msg.get("info", {})
            parts = msg.get("parts", [])

            if info.get("role") == "assistant":
                msg_tokens = info.get("tokens", {})
                total_tokens["input"] += msg_tokens.get("input", 0)
                total_tokens["output"] += msg_tokens.get("output", 0)
                total_tokens["reasoning"] += msg_tokens.get("reasoning", 0)
                total_cost += info.get("cost", 0) or 0

            for part in parts:
                part_type = part.get("type", "")
                if part_type == "step-start":
                    llm_calls += 1
                elif part_type == "tool":
                    tool_calls.append(part.get("tool", "unknown"))
                elif part_type == "text":
                    text = part.get("text", "")
                    if text:
                        text_outputs.append(text)

        final_output = None
        if text_outputs:
            final_output = " ".join(text_outputs)[-500:]

        return {
            "final_output": final_output,
            "llm_calls": llm_calls,
            "tool_calls": tool_calls,
            "errors": errors,
            "total_tokens": total_tokens,
            "total_cost": total_cost,
            "api_messages": len(messages),
            "messages": messages,
        }

    def get_last_assistant_text(self, messages: Optional[List]) -> str:
        """Return the text of the last assistant message, or empty string.

        Args:
            messages: List of OpenCode API messages

        Returns:
            Text content of the last assistant message.
        """
        if not messages:
            return ""
        for msg in reversed(messages):
            if not isinstance(msg, dict):
                continue
            role = msg.get("role", msg.get("type", ""))
            if role not in ("assistant", "model"):
                continue
            content = msg.get("content", msg.get("text", ""))
            if isinstance(content, list):
                content = " ".join(
                    p.get("text", str(p)) if isinstance(p, dict) else str(p)
                    for p in content
                )
            elif not isinstance(content, str):
                content = str(content)
            return content.strip()
        return ""

    def extract_context_from_messages(
        self, messages: Optional[List], max_chars: int = 2000
    ) -> str:
        """Best-effort extraction of readable context from session messages.

        Args:
            messages: List of OpenCode API messages
            max_chars: Maximum characters to extract

        Returns:
            Concatenated context string.
        """
        if not messages:
            return ""
        context_parts: List[str] = []
        for msg in reversed(messages):
            if not isinstance(msg, dict):
                continue
            role = msg.get("role", msg.get("type", "unknown"))
            content = msg.get("content", msg.get("text", ""))
            if isinstance(content, list):
                content = " ".join(
                    p.get("text", str(p)) if isinstance(p, dict) else str(p)
                    for p in content
                )
            elif not isinstance(content, str):
                content = str(content)
            if content.strip():
                context_parts.append(f"[{role}]: {content[:500]}")
            if sum(len(p) for p in context_parts) >= max_chars:
                break
        context_parts.reverse()
        return "\n".join(context_parts)


class OpenCodeAgent(OpenCodeClient):
    """Extended base class for agents with session lifecycle management.

    Adds:
    - Signal handling for graceful shutdown
    - Session tracking with done phrase detection
    - Helper methods for Trident integration
    """

    # Done phrases to detect when agent considers work complete
    _DONE_PHRASES = (
        "all tasks completed",
        "all tasks are completed",
        "all tasks have been completed",
        "completed all tasks",
        "mission complete",
        "objective complete",
        "target compromised",
        "finished the attack",
        "attack complete",
        "workday is complete",
        "workday is done",
        "workday complete",
        "finished all tasks",
        "completed my workday",
        "nothing left to do",
        "no more tasks",
        "signing off",
        "end of workday",
        "logging off",
        "work is done",
        "that concludes",
        "that's all for today",
        "all done for today",
        "wrapping up for the day",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._signal_log_ctx: Optional[Dict] = None
        self._setup_signal_handlers()

    def _setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown."""
        def _signal_handler(signum, frame):
            """Abort the running session (if any), flush logs, and exit."""
            if self._active_session_id:
                # Best-effort save of current session logs
                ctx = self._signal_log_ctx
                if ctx:
                    try:
                        self.save_session_logs(
                            self._active_session_id,
                            ctx["output_dir"],
                            ctx["timeline_path"],
                            ctx["execution_id"],
                            ctx["session_count"],
                        )
                    except Exception:
                        pass
                    # Write DONE entry to timeline
                    try:
                        self._write_timeline_entry(
                            ctx["timeline_path"], "INTERRUPTED",
                            "Execution interrupted by signal",
                            data={"exec": ctx["execution_id"][:8],
                                  "signal": signum})
                    except Exception:
                        pass
                self.abort_session(self._active_session_id)
            sys.exit(1)

        signal.signal(signal.SIGTERM, _signal_handler)
        signal.signal(signal.SIGINT, _signal_handler)

    def _write_timeline_entry(
        self, path: str, level: str, message: str, data: Optional[dict] = None
    ) -> None:
        """Write an entry to the timeline file.

        Args:
            path: Timeline file path
            level: Log level
            message: Message text
            data: Optional additional data
        """
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": level.upper(),
            "msg": message,
        }
        if data:
            entry["data"] = data

        # Split path into directory and filename for unified function
        directory = os.path.dirname(path)
        filename = os.path.basename(path)

        write_timeline_entry(directory, filename, entry)

    def agent_says_done(self, messages: Optional[List]) -> bool:
        """Check if the agent's last response signals task completion.

        Args:
            messages: List of OpenCode API messages

        Returns:
            True if agent signals completion, False otherwise.
        """
        text = self.get_last_assistant_text(messages).lower()
        if not text:
            return False
        return any(phrase in text for phrase in self._DONE_PHRASES)

    def get_trident_base(self) -> str:
        """Get Trident base directory.

        Returns:
            Path to Trident base directory.
        """
        trident_home = os.environ.get("TRIDENT_HOME", "").strip()
        if trident_home and os.path.isdir(trident_home):
            return trident_home

        script_dir = os.path.dirname(os.path.abspath(__file__))
        current = script_dir
        for _ in range(5):
            if os.path.exists(os.path.join(current, "README.md")) and \
               os.path.exists(os.path.join(current, "docker-compose.yml")):
                return current
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent

        return os.getcwd()

    def resolve_run_id(self) -> str:
        """Resolve the current RUN_ID.

        Returns:
            Current run ID.
        """
        run_id = os.environ.get("RUN_ID", "").strip()
        if run_id:
            return run_id
        base_dir = self.get_trident_base()
        current_run = os.path.join(base_dir, "outputs", ".current_run")
        try:
            with open(current_run, "r", encoding="utf-8") as fh:
                return fh.read().strip()
        except FileNotFoundError:
            return "manual"
