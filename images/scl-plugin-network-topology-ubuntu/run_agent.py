#!/usr/bin/env python3
"""
OpenCode Generic Agent Launcher

This script provides a command-line interface for launching OpenCode agents
with various configuration options. It supports launching any agent type
defined in opencode_config.py.

Usage:
    python run_agent.py --agent <agent_type> --goal "<prompt>" [options]

Examples:
    # Run a coding task
    python run_agent.py --agent coder56 --goal "Fix the bug in auth.py"

    # Run a database analysis with custom timeout
    python run_agent.py --agent db_admin --goal "Analyze schema.sql" --time-limit 120

    # Run security analysis with custom logging
    python run_agent.py --agent soc_god --goal "Review access logs" --log-dir ./logs
"""

import argparse
import sys
import os
import time
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

# Import our configuration module
try:
    from opencode_config import (
        AGENT_TEMPLATES,
        generate_opencode_config,
        list_available_agents
    )
    from opencode_generic_client import OpenCodeClient
except ImportError as e:
    print(f"Error importing required modules: {e}", file=sys.stderr)
    print("Ensure opencode_config.py and opencode_generic_client.py are in the same directory.", file=sys.stderr)
    sys.exit(1)


# ============================================================================
# ARGUMENT PARSING
# ============================================================================

def parse_arguments() -> argparse.Namespace:
    """
    Parse and validate command-line arguments.

    Returns:
        Namespace containing parsed arguments
    """
    parser = argparse.ArgumentParser(
        description="OpenCode Generic Agent Launcher",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Available agent types:
  db_admin   - Database Administrator (schema, queries, optimization)
  coder56    - Expert Software Developer (full-stack, multiple languages)
  soc_god    - Supreme Security Analyst (threat detection, incident response)

Examples:
  %(prog)s --agent coder56 --goal "Write a REST API for user management"
  %(prog)s --agent db_admin --goal "Optimize this query" --time-limit 60
  %(prog)s --agent soc_god --goal "Analyze auth.log for suspicious activity" --log-dir ./logs
        """
    )

    # Required arguments
    parser.add_argument(
        "--agent", "-a",
        type=str,
        required=True,
        choices=list(AGENT_TEMPLATES.keys()),
        help="Type of agent to launch"
    )

    parser.add_argument(
        "--goal", "-g",
        type=str,
        required=True,
        help="The task/prompt to send to the agent"
    )

    # Optional connection arguments
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="OpenCode server host (default: 127.0.0.1)"
    )

    parser.add_argument(
        "--port", "-p",
        type=int,
        default=4096,
        help="OpenCode server port (default: 4096)"
    )

    # Time and session arguments
    parser.add_argument(
        "--time-limit", "-t",
        type=int,
        default=300,
        help="Maximum time to wait for completion in seconds (default: 300)"
    )

    parser.add_argument(
        "--delay", "-d",
        type=int,
        default=0,
        help="Delay in seconds before starting the task (default: 0)"
    )

    # Logging arguments
    parser.add_argument(
        "--log-dir", "-l",
        type=str,
        default=None,
        help="Directory for log files (default: current directory)"
    )

    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Logging level (default: INFO)"
    )

    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Suppress console output, only log to file"
    )

    # Session management
    parser.add_argument(
        "--session-id",
        type=str,
        default=None,
        help="Custom session ID (default: auto-generated)"
    )

    parser.add_argument(
        "--no-cleanup",
        action="store_true",
        help="Don't automatically cleanup session after completion"
    )

    return parser.parse_args()


# ============================================================================
# LOGGING SETUP
# ============================================================================

def setup_logging(
    log_dir: Optional[str],
    log_level: str,
    quiet: bool,
    agent_name: str
) -> logging.Logger:
    """
    Configure logging for the agent run.

    Args:
        log_dir: Directory for log files (None for current directory)
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
        quiet: If True, suppress console output
        agent_name: Name of the agent for log file naming

    Returns:
        Configured logger instance
    """
    # Create logger
    logger = logging.getLogger(f"run_agent.{agent_name}")
    logger.setLevel(getattr(logging, log_level))

    # Create log directory if specified
    if log_dir:
        Path(log_dir).mkdir(parents=True, exist_ok=True)

    # Generate timestamp for unique log file
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_filename = f"{agent_name}_{timestamp}.log"
    log_path = os.path.join(log_dir or ".", log_filename) if log_dir else log_filename

    # File handler
    file_handler = logging.FileHandler(log_path)
    file_handler.setLevel(getattr(logging, log_level))
    file_formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)

    # Console handler (skip if quiet)
    if not quiet:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(getattr(logging, log_level))
        console_formatter = logging.Formatter("%(levelname)s: %(message)s")
        console_handler.setFormatter(console_formatter)
        logger.addHandler(console_handler)

    logger.info(f"Logging initialized. Log file: {os.path.abspath(log_path)}")

    return logger


# ============================================================================
# AGENT EXECUTION
# ============================================================================

def run_agent(
    agent_type: str,
    goal: str,
    host: str,
    port: int,
    time_limit: int,
    delay: int,
    session_id: Optional[str],
    no_cleanup: bool,
    logger: logging.Logger
) -> int:
    """
    Execute the agent with the given parameters.

    Args:
        agent_type: Type of agent to run
        goal: Task/prompt for the agent
        host: Server hostname
        port: Server port
        time_limit: Maximum execution time in seconds
        delay: Delay before starting
        session_id: Custom session ID
        no_cleanup: Skip session cleanup
        logger: Logger instance

    Returns:
        Exit code (0 for success, non-zero for failure)
    """
    try:
        # Apply delay if specified
        if delay > 0:
            logger.info(f"Waiting {delay} seconds before starting...")
            time.sleep(delay)

        # Get agent configuration
        logger.info(f"Initializing agent: {agent_type}")
        agent_config = AGENT_TEMPLATES[agent_type]

        # Initialize client
        logger.info(f"Connecting to OpenCode at {host}:{port}")
        client = OpenCodeClient(
            host=host,
            port=port,
            timeout=time_limit,
            logger=logger
        )

        # Create session
        logger.info("Creating session...")
        session_info = client.create_session(
            agent_type=agent_type,
            system_prompt=agent_config["prompt"],
            session_id=session_id
        )
        logger.info(f"Session created: {session_info.get('session_id', 'unknown')}")

        # Send prompt
        logger.info("Sending task to agent...")
        logger.debug(f"Task: {goal[:100]}...")

        result = client.send_prompt(goal)

        # Handle result
        if result.get("success"):
            logger.info("Task completed successfully")
            response = result.get("response", "")

            if response:
                logger.info("=== Agent Response ===")
                for line in response.split("\n"):
                    logger.info(line)
                logger.info("=== End Response ===")

            return 0
        else:
            error = result.get("error", "Unknown error")
            logger.error(f"Task failed: {error}")
            return 1

    except TimeoutError as e:
        logger.error(f"Timeout after {time_limit} seconds: {e}")
        return 2

    except ConnectionError as e:
        logger.error(f"Connection error: {e}")
        return 3

    except KeyboardInterrupt:
        logger.info("Task cancelled by user")
        return 130

    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        return 4

    finally:
        # Cleanup if needed
        if not no_cleanup and 'client' in locals():
            logger.info("Cleaning up session...")
            try:
                client.close_session()
            except Exception as e:
                logger.warning(f"Cleanup warning: {e}")


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

def main() -> int:
    """
    Main entry point for the agent launcher.

    Returns:
        Exit code
    """
    args = parse_arguments()

    # Setup logging
    logger = setup_logging(
        log_dir=args.log_dir,
        log_level=args.log_level,
        quiet=args.quiet,
        agent_name=args.agent
    )

    logger.info("=" * 60)
    logger.info(f"OpenCode Agent Launcher: {args.agent}")
    logger.info(f"Goal: {args.goal[:100]}...")
    logger.info("=" * 60)

    # Run the agent
    exit_code = run_agent(
        agent_type=args.agent,
        goal=args.goal,
        host=args.host,
        port=args.port,
        time_limit=args.time_limit,
        delay=args.delay,
        session_id=args.session_id,
        no_cleanup=args.no_cleanup,
        logger=logger
    )

    logger.info(f"Agent run completed with exit code: {exit_code}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
