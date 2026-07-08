"""
Shared constants for all Trident agents.

This module defines constants that should be used consistently
across all agent implementations to ensure uniform behavior.
"""

# Grace period for operations - the time allowed for transitions
# between states (e.g., between starting a service and using it)
# 15 seconds provides adequate time for most operations while
# avoiding excessive delays
GRACE_PERIOD_SECONDS = 15
