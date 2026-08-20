"""
Shared AWS dev account, scheduled 07:00-20:00 weekdays (§8.3).

Real AWS services against a free-tier-shaped stack. Off-hours shutdown saves
roughly 65% (§10.3 item 7).
"""

from .base import *  # noqa: F401,F403
