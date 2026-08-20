"""
Staging — mirrors the production topology at smaller size (§8.3).

Secrets come from SSM Parameter Store; nothing reads a secret from a file here.
Real bid sets only with CBC consent. This is where threshold calibration and
golden-set runs happen before a release.
"""

from .base import *  # noqa: F401,F403
