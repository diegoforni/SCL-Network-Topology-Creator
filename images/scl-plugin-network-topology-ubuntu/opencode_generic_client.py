#!/usr/bin/env python3
"""
OpenCode Generic Client

This module provides a Python client for connecting to and interacting with
the OpenCode server running at 127.0.0.1:4096. It handles session management,
prompt submission, response collection, and logging.

Features:
- TCP socket connection to OpenCode server
- Session creation and management
- Async prompt submission with completion waiting
- Comprehensive logging to file
- Configurable timeouts and retries
"""

import socket
import json
import logging
import uuid
import time
from typing import Optional, Dict, Any, List
from pathlib import Path
from datetime import datetime
import threading


# ============================================================================
# EXCEPTIONS
# ============================================================================

class OpenCodeError(Exception):
    """Base exception for OpenCode client errors."""
    pass


class OpenCodeConnectionError(OpenCodeError):
    """Raised when connection to OpenCode server fails."""
    pass


class OpenCodeTimeoutError(OpenCodeError):
    """Raised when an operation times out."""
    pass


class OpenCodeSessionError(OpenCodeError):
    """Raised when session operations fail."""
    pass


# ============================================================================
# CLIENT IMPLEMENTATION
# ============================================================================

class OpenCodeClient:
    """
    Client for interacting with the OpenCode server.

    This client manages TCP connections, session lifecycle, and prompt/response
    communication with the OpenCode agent server.

    Attributes:
        host: Server hostname or IP address
        port: Server port number
        timeout: Default timeout for operations in seconds
        logger: Logger instance for output
    """

    DEFAULT_HOST = "127.0.0.1"
    DEFAULT_PORT = 4096
    DEFAULT_TIMEOUT = 300
    BUFFER_SIZE = 65536  # 64KB buffer for receiving responses

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        timeout: int = DEFAULT_TIMEOUT,
        logger: Optional[logging.Logger] = None,
        log_file: Optional[str] = None
    ):
        """
        Initialize the OpenCode client.

        Args:
            host: Server hostname (default: 127.0.0.1)
            port: Server port (default: 4096)
            timeout: Operation timeout in seconds (default: 300)
            logger: Optional logger instance
            log_file: Optional path to log file
        """
        self.host = host
        self.port = port
        self.timeout = timeout
        self._socket: Optional[socket.socket] = None
        self._session_id: Optional[str] = None
        self._connected = False

        # Setup logging
        if logger is None:
            self._setup_default_logger(log_file)
        else:
            self.logger = logger

        self.logger.debug(f"OpenCodeClient initialized: {host}:{port}")

    def _setup_default_logger(self, log_file: Optional[str]) -> None:
        """
        Setup a default logger if none provided.

        Args:
            log_file: Optional path to log file
        """
        self.logger = logging.getLogger(f"OpenCodeClient.{id(self)}")
        self.logger.setLevel(logging.DEBUG)

        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        console_handler.setFormatter(console_formatter)
        self.logger.addHandler(console_handler)

        # File handler if specified
        if log_file:
            file_handler = logging.FileHandler(log_file)
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(console_formatter)
            self.logger.addHandler(file_handler)

    # ========================================================================
    # CONNECTION MANAGEMENT
    # ========================================================================

    def connect(self, retry_attempts: int = 3, retry_delay: float = 1.0) -> None:
        """
        Establish TCP connection to OpenCode server.

        Args:
            retry_attempts: Number of connection retries (default: 3)
            retry_delay: Delay between retries in seconds (default: 1.0)

        Raises:
            OpenCodeConnectionError: If connection fails after all retries
        """
        if self._connected:
            self.logger.warning("Already connected to server")
            return

        last_error = None
        for attempt in range(retry_attempts):
            try:
                self.logger.info(
                    f"Connecting to {self.host}:{self.port} "
                    f"(attempt {attempt + 1}/{retry_attempts})"
                )
                self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self._socket.settimeout(self.timeout)
                self._socket.connect((self.host, self.port))
                self._connected = True
                self.logger.info("Successfully connected to OpenCode server")
                return

            except socket.error as e:
                last_error = e
                self.logger.warning(f"Connection attempt {attempt + 1} failed: {e}")
                if attempt < retry_attempts - 1:
                    time.sleep(retry_delay)

        raise OpenCodeConnectionError(
            f"Failed to connect to {self.host}:{self.port} after {retry_attempts} attempts: {last_error}"
        )

    def disconnect(self) -> None:
        """Close the TCP connection to the server."""
        if self._socket:
            try:
                self._socket.close()
                self.logger.debug("Connection closed")
            except socket.error as e:
                self.logger.warning(f"Error closing socket: {e}")
            finally:
                self._socket = None
                self._connected = False

    def is_connected(self) -> bool:
        """
        Check if currently connected to the server.

        Returns:
            True if connected, False otherwise
        """
        return self._connected and self._socket is not None

    # ========================================================================
    # SESSION MANAGEMENT
    # ========================================================================

    def create_session(
        self,
        agent_type: str,
        system_prompt: str,
        session_id: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Create a new agent session on the OpenCode server.

        Args:
            agent_type: Type of agent to create (e.g., "coder56", "db_admin")
            system_prompt: System prompt to configure the agent
            session_id: Optional custom session ID (auto-generated if None)
            config: Optional additional configuration parameters

        Returns:
            Dictionary with session information including session_id

        Raises:
            OpenCodeSessionError: If session creation fails
            OpenCodeConnectionError: If not connected to server
        """
        if not self.is_connected():
            self.connect()

        # Generate session ID if not provided
        if session_id is None:
            session_id = f"session_{uuid.uuid4().hex[:16]}"

        self._session_id = session_id

        # Prepare session request
        request = {
            "action": "create_session",
            "session_id": session_id,
            "agent_type": agent_type,
            "system_prompt": system_prompt,
            "config": config or {}
        }

        self.logger.info(f"Creating session '{session_id}' for agent '{agent_type}'")
        self.logger.debug(f"Session request: {json.dumps(request, indent=2)}")

        try:
            # Send request and get response
            response = self._send_request(request)

            if response.get("status") == "success":
                self.logger.info(f"Session created successfully: {session_id}")
                return {
                    "session_id": session_id,
                    "agent_type": agent_type,
                    "status": "active",
                    "server_response": response
                }
            else:
                raise OpenCodeSessionError(
                    f"Session creation failed: {response.get('error', 'Unknown error')}"
                )

        except socket.error as e:
            raise OpenCodeConnectionError(f"Communication error: {e}")

    def close_session(self) -> None:
        """
        Close the current session on the server.

        Raises:
            OpenCodeSessionError: If session closure fails
        """
        if not self._session_id:
            self.logger.warning("No active session to close")
            return

        try:
            request = {
                "action": "close_session",
                "session_id": self._session_id
            }

            self.logger.info(f"Closing session '{self._session_id}'")
            response = self._send_request(request)

            if response.get("status") == "success":
                self.logger.info(f"Session '{self._session_id}' closed successfully")
            else:
                self.logger.warning(
                    f"Session close warning: {response.get('error', 'Unknown error')}"
                )

        except Exception as e:
            self.logger.warning(f"Error closing session: {e}")
        finally:
            self._session_id = None

    def get_session_info(self) -> Dict[str, Any]:
        """
        Get information about the current session.

        Returns:
            Dictionary with session information

        Raises:
            OpenCodeSessionError: If no active session
        """
        if not self._session_id:
            raise OpenCodeSessionError("No active session")

        request = {
            "action": "get_session_info",
            "session_id": self._session_id
        }

        response = self._send_request(request)
        return response.get("data", {})

    # ========================================================================
    # PROMPT/RESPONSE COMMUNICATION
    # ========================================================================

    def send_prompt(
        self,
        prompt: str,
        timeout: Optional[int] = None,
        stream: bool = False
    ) -> Dict[str, Any]:
        """
        Send a prompt to the agent and wait for completion.

        Args:
            prompt: The user prompt/task to send
            timeout: Custom timeout in seconds (uses default if None)
            stream: If True, yield streaming responses (not yet implemented)

        Returns:
            Dictionary with:
                - success (bool): Whether the prompt was processed successfully
                - response (str): The agent's response text
                - metadata (dict): Additional metadata about the response
                - error (str): Error message if success is False

        Raises:
            OpenCodeTimeoutError: If operation times out
            OpenCodeConnectionError: If communication fails
        """
        if not self.is_connected():
            self.connect()

        if not self._session_id:
            raise OpenCodeSessionError("No active session. Call create_session() first.")

        effective_timeout = timeout if timeout is not None else self.timeout

        request = {
            "action": "process_prompt",
            "session_id": self._session_id,
            "prompt": prompt,
            "stream": stream
        }

        self.logger.info(f"Sending prompt (length: {len(prompt)} characters)")
        self.logger.debug(f"Prompt preview: {prompt[:200]}...")

        try:
            start_time = time.time()

            # Send the request
            response = self._send_request(request, timeout=effective_timeout)

            elapsed_time = time.time() - start_time

            # Process the response
            if response.get("status") == "success":
                self.logger.info(
                    f"Prompt completed successfully in {elapsed_time:.2f} seconds"
                )
                return {
                    "success": True,
                    "response": response.get("response", ""),
                    "metadata": {
                        "elapsed_time": elapsed_time,
                        "tokens_used": response.get("tokens_used"),
                        "model": response.get("model")
                    }
                }
            else:
                error_msg = response.get("error", "Unknown error")
                self.logger.error(f"Prompt processing failed: {error_msg}")
                return {
                    "success": False,
                    "error": error_msg,
                    "response": response.get("partial_response", ""),
                    "metadata": {"elapsed_time": elapsed_time}
                }

        except socket.timeout:
            raise OpenCodeTimeoutError(
                f"Operation timed out after {effective_timeout} seconds"
            )
        except socket.error as e:
            raise OpenCodeConnectionError(f"Communication error: {e}")

    def send_prompt_async(self, prompt: str) -> threading.Thread:
        """
        Send a prompt asynchronously and return a thread.

        Args:
            prompt: The user prompt/task to send

        Returns:
            Thread object that can be joined to wait for completion

        Note:
            This is a simplified async implementation. For production use,
            consider using asyncio or a proper async framework.
        """
        result_container = {"result": None, "error": None}

        def _run():
            try:
                result_container["result"] = self.send_prompt(prompt)
            except Exception as e:
                result_container["error"] = e

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        thread.result_container = result_container
        return thread

    # ========================================================================
    # LOW-LEVEL COMMUNICATION
    # ========================================================================

    def _send_request(
        self,
        request: Dict[str, Any],
        timeout: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Send a JSON request and receive a JSON response.

        Args:
            request: Dictionary to send as JSON
            timeout: Optional custom timeout for this request

        Returns:
            Parsed JSON response as dictionary

        Raises:
            socket.error: If communication fails
        """
        if not self._socket:
            raise OpenCodeConnectionError("Not connected to server")

        # Set timeout for this request
        original_timeout = self._socket.gettimeout()
        if timeout is not None:
            self._socket.settimeout(timeout)

        try:
            # Serialize and send request
            request_json = json.dumps(request) + "\n"
            request_bytes = request_json.encode("utf-8")

            self.logger.debug(f"Sending request: {len(request_bytes)} bytes")
            self._socket.sendall(request_bytes)

            # Receive response
            response_data = b""
            while True:
                chunk = self._socket.recv(self.BUFFER_SIZE)
                if not chunk:
                    break
                response_data += chunk
                # Check if we have a complete JSON response
                if b"\n" in response_data:
                    break

            # Parse response
            response_text = response_data.decode("utf-8").strip()
            self.logger.debug(f"Received response: {len(response_data)} bytes")

            try:
                response = json.loads(response_text)
                return response
            except json.JSONDecodeError as e:
                self.logger.error(f"Invalid JSON response: {response_text[:200]}")
                raise OpenCodeError(f"Invalid JSON response from server: {e}")

        finally:
            # Restore original timeout
            self._socket.settimeout(original_timeout)

    # ========================================================================
    # UTILITY METHODS
    # ========================================================================

    def ping(self) -> bool:
        """
        Check if the server is responsive.

        Returns:
            True if server responds, False otherwise
        """
        try:
            if not self.is_connected():
                self.connect(retry_attempts=1)

            request = {"action": "ping"}
            response = self._send_request(request)
            return response.get("status") == "pong"

        except Exception as e:
            self.logger.debug(f"Ping failed: {e}")
            return False

    def get_server_info(self) -> Dict[str, Any]:
        """
        Get information about the OpenCode server.

        Returns:
            Dictionary with server information
        """
        if not self.is_connected():
            self.connect()

        request = {"action": "get_server_info"}
        response = self._send_request(request)
        return response.get("data", {})

    def __enter__(self):
        """Context manager entry."""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        try:
            self.close_session()
        finally:
            self.disconnect()


# ============================================================================
# LOG FILE HANDLER
# ============================================================================

class OpenCodeLogger:
    """
    Utility class for managing OpenCode session logs.

    This class provides convenient methods for creating, writing to, and
    managing log files for OpenCode sessions.
    """

    def __init__(self, log_dir: str = "./opencode_logs"):
        """
        Initialize the logger.

        Args:
            log_dir: Directory for log files
        """
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.current_log_file: Optional[Path] = None

    def create_session_log(self, session_id: str, agent_type: str) -> Path:
        """
        Create a new log file for a session.

        Args:
            session_id: Unique session identifier
            agent_type: Type of agent

        Returns:
            Path to the created log file
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{agent_type}_{session_id}_{timestamp}.log"
        self.current_log_file = self.log_dir / filename
        self.current_log_file.touch()
        return self.current_log_file

    def log(self, message: str, level: str = "INFO") -> None:
        """
        Write a log message to the current log file.

        Args:
            message: Message to log
            level: Log level (INFO, WARNING, ERROR, DEBUG)
        """
        if self.current_log_file is None:
            raise RuntimeError("No log file created. Call create_session_log() first.")

        timestamp = datetime.now().isoformat()
        log_entry = f"{timestamp} - {level} - {message}\n"

        with open(self.current_log_file, "a") as f:
            f.write(log_entry)

    def log_request(self, request: Dict[str, Any]) -> None:
        """Log a request dictionary."""
        self.log(f"REQUEST: {json.dumps(request, indent=2)}")

    def log_response(self, response: Dict[str, Any]) -> None:
        """Log a response dictionary."""
        self.log(f"RESPONSE: {json.dumps(response, indent=2)}")

    def log_error(self, error: str) -> None:
        """Log an error message."""
        self.log(f"ERROR: {error}", level="ERROR")

    def get_log_path(self) -> Optional[Path]:
        """
        Get the path to the current log file.

        Returns:
            Path to log file or None if no file created
        """
        return self.current_log_file


# ============================================================================
# MAIN - FOR TESTING
# ============================================================================

if __name__ == "__main__":
    import sys

    # Simple test client
    print("OpenCode Generic Client - Test Mode")
    print("=" * 50)

    # Test connection
    client = OpenCodeClient()

    try:
        if client.ping():
            print("✓ Server is responsive")
            server_info = client.get_server_info()
            print(f"✓ Server info: {json.dumps(server_info, indent=2)}")
        else:
            print("✗ Server is not responding")
            sys.exit(1)

    except Exception as e:
        print(f"✗ Error: {e}")
        sys.exit(1)
    finally:
        client.disconnect()

    print("\nTest completed successfully")
