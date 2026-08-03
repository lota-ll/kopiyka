"""Категоризація за MCC-кодом (ISO 18245).

Перший і найточніший рівень категоризації. Працює для monobank, де MCC
присутній у виписці. Для Приват24 MCC зазвичай відсутній — там спрацьовує
наступний рівень (regex по опису, тиждень 5).

Порядок пріоритетів у повному рушії:
    ручний override користувача → regex-правило → MCC → 'other'
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

# У dev-режимі пакет лежить у src/, і seeds/ поруч із репо. У контейнері
# пакет встановлено у venv, тому шлях задається змінною оточення.
_DEV_SEEDS = Path(__file__).resolve().parents[3] / "seeds"
SEEDS_DIR = Path(os.environ.get("KOPIYKA_SEEDS_DIR", _DEV_SEEDS))
SEEDS = SEEDS_DIR / "categories.yaml"


@lru_cache
def mcc_map() -> dict[int, str]:
    """Повертає ``{mcc: category_slug}``."""
    if not SEEDS.exists():
        raise FileNotFoundError(
            f"не знайдено {SEEDS}; задай KOPIYKA_SEEDS_DIR або запускай з кореня репозиторію"
        )
    data = yaml.safe_load(SEEDS.read_text("utf-8"))
    mapping: dict[int, str] = {}
    for category in data:
        for code in category.get("mcc") or []:
            mapping[int(code)] = category["slug"]
    return mapping


def category_for_mcc(mcc: int | None) -> str | None:
    """Категорія за MCC або ``None``, якщо код невідомий."""
    if mcc is None:
        return None
    return mcc_map().get(mcc)
