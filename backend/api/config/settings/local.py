"""
Local development: Docker Compose + MiniStack (§8.3).

Textract and Bedrock are the only services stubbed. ``FAKE_OCR=1`` replays a
recorded OCR JSON so the whole pipeline runs with no AWS calls and no spend.
"""

from .base import *  # noqa: F401,F403
from .base import ALLOWED_HOSTS

# Django's test client and pytest-django both address the app as "testserver".
# Added here rather than in base.py so it can never be reachable in a deployed
# environment.
if "testserver" not in ALLOWED_HOSTS:
    ALLOWED_HOSTS = [*ALLOWED_HOSTS, "testserver", "api", "localhost", "127.0.0.1"]

# The local LLM escape hatch is reachable only from this settings module, and only
# when the operator sets NIM_API_KEY. shared.config refuses to build a client in
# any other environment (§11.1, open item Q2).
