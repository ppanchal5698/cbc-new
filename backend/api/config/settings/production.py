"""
Production (§3.1 topology, §8.3).

Security hardening (HSTS, secure cookies, SSL redirect) is applied in base.py
under ``ENVIRONMENT == PROD``, so it cannot be lost by someone importing the
wrong settings module. Secrets come exclusively from SSM.
"""

from .base import *  # noqa: F401,F403
