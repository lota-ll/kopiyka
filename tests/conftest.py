"""Спільні фікстури тестів."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Тести за замовчуванням працюють у локальному dev-режимі автентифікації.
os.environ.setdefault("KOPIYKA_ENV", "local")
os.environ.setdefault("KOPIYKA_AUTH_MODE", "dev")
os.environ.setdefault("KOPIYKA_INVITE_ONLY", "false")

if os.environ.get("KOPIYKA_TEST_DATABASE_URL"):
    os.environ["KOPIYKA_DATABASE_URL"] = os.environ["KOPIYKA_TEST_DATABASE_URL"]
