#!/usr/bin/env python3
"""
Shared type definitions for Trident agents.

Defines standard TypedDict structures for agent metrics and return types
to ensure consistency across all agents (coder56, db_admin, attacker, defender, etc.).
"""

from typing import TypedDict, List, Optional, Dict, Any


class TokenCount(TypedDict, total=False):
    """Token usage breakdown."""
    input: int
    output: int
    reasoning: int


class AgentMetrics(TypedDict, total=False):
    """
    Standard agent execution metrics.

    All agents should return a dict matching this structure.
    Required fields are marked; optional fields may be omitted if not available.

    Required:
        final_output: str
        llm_calls: int
        tool_calls: List[str]
        errors: List[Any]

    Optional (recommended when available):
        total_tokens: Optional[int]  # Total token count (sum of input+output)
        total_cost: Optional[float]   # Total cost in USD
        api_messages: Optional[int]   # Number of messages in API format
        messages: Optional[int]       # Number of messages (alternative name)

    Example:
        {
            "final_output": "Remediation plan executed successfully",
            "llm_calls": 3,
            "tool_calls": ["bash", "read", "write"],
            "errors": [],
            "total_tokens": 15234,
            "total_cost": 0.05,
            "api_messages": 5,
            "messages": 5
        }
    """
    final_output: Optional[str]
    llm_calls: int
    tool_calls: List[str]
    errors: List[Any]
    total_tokens: Optional[int]
    total_cost: Optional[float]
    api_messages: Optional[int]
    messages: Optional[int]


def create_agent_metrics(
    final_output: Optional[str] = None,
    llm_calls: int = 0,
    tool_calls: Optional[List[str]] = None,
    errors: Optional[List[Any]] = None,
    total_tokens: Optional[int] = None,
    total_cost: Optional[float] = None,
    api_messages: Optional[int] = None,
    messages: Optional[int] = None,
) -> AgentMetrics:
    """
    Helper function to create a properly structured AgentMetrics dict.

    Args:
        final_output: The final output text from the agent
        llm_calls: Number of LLM calls made
        tool_calls: List of tool names called
        errors: List of errors encountered
        total_tokens: Total token count (sum of input+output)
        total_cost: Total cost in USD
        api_messages: Number of messages in API format
        messages: Number of messages (alternative name)

    Returns:
        AgentMetrics dict
    """
    return AgentMetrics(
        final_output=final_output,
        llm_calls=llm_calls,
        tool_calls=tool_calls or [],
        errors=errors or [],
        total_tokens=total_tokens,
        total_cost=total_cost,
        api_messages=api_messages,
        messages=messages,
    )


def ensure_full_metrics(metrics: Dict[str, Any]) -> AgentMetrics:
    """
    Ensure a metrics dict has all required AgentMetrics fields.

    This function can be used to normalize partial metrics from older agents
    to the full AgentMetrics structure.

    Args:
        metrics: A potentially incomplete metrics dict

    Returns:
        AgentMetrics dict with all required fields populated
    """
    return AgentMetrics(
        final_output=metrics.get("final_output"),
        llm_calls=metrics.get("llm_calls", 0),
        tool_calls=metrics.get("tool_calls", []),
        errors=metrics.get("errors", []),
        total_tokens=metrics.get("total_tokens"),
        total_cost=metrics.get("total_cost"),
        api_messages=metrics.get("api_messages") or metrics.get("messages"),
        messages=metrics.get("messages") or metrics.get("api_messages"),
    )
