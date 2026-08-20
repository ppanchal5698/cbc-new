"""
Shared Django bootstrap for the operator scripts.

These run against the same database the API owns, using the same models, because
an operator report that reimplements a query is an operator report that drifts
away from what the application actually did. There is no second ORM and no raw
SQL here on purpose.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

#: /app in the container, backend/ in a checkout.
BACKEND_ROOT = Path(__file__).resolve().parents[2] / "backend"
if not BACKEND_ROOT.exists():
    # Mounted layout: ops/ sits inside the backend root rather than beside it.
    BACKEND_ROOT = Path(__file__).resolve().parents[2]


def setup() -> None:
    """Configure Django so ``from projects.models import ...`` works."""
    import django

    for path in (str(BACKEND_ROOT), str(BACKEND_ROOT / "api")):
        if path not in sys.path:
            sys.path.insert(0, path)

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
    django.setup()
