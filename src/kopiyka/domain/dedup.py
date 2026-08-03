"""Детермінований ключ дедуплікації транзакцій.

Проблема: виписки перекриваються. Користувач заллє «січень», потім
«грудень–лютий» — і половина рядків повториться. У monobank API є свій
``id``, у CSV-експорті його немає. Тому потрібен ключ, який обчислюється
з самих даних і дає той самий результат при повторному заливі.

Ключ навмисно **не включає** баланс після операції та MCC: ці поля банк
може віддати по-різному в різних експортах того самого періоду.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime

# Версія алгоритму. Зміна нормалізації = зміна версії = потрібен
# перерахунок хешів існуючих рядків (див. RUNBOOK).
DEDUP_VERSION = "v1"

_WS = re.compile(r"\s+")
_NOISE = re.compile(r"[^\w\s]", flags=re.UNICODE)


def normalize_description(raw: str) -> str:
    """Приводить опис операції до канонічного вигляду.

    Банки міняють регістр, кількість пробілів і пунктуацію між експортами,
    тому все це прибирається. Кирилиця зберігається (NFKC, не транслітерація).

    >>> normalize_description("  SILPO  ,  KYIV\\u00a0 ")
    'silpo kyiv'
    """
    text = unicodedata.normalize("NFKC", raw)
    text = text.replace("\u00a0", " ").replace("\u202f", " ")
    text = _NOISE.sub(" ", text)
    text = _WS.sub(" ", text)
    return text.strip().lower()


def dedup_hash(
    *,
    account_ref: str,
    booked_at: datetime,
    amount_minor: int,
    currency: str,
    description: str,
) -> bytes:
    """Повертає 32-байтовий SHA-256 ключ транзакції.

    ``booked_at`` округлюється до хвилини: приват у частині експортів не
    віддає секунди, і без округлення той самий платіж дасть два різні хеші.
    """
    stamp = booked_at.replace(second=0, microsecond=0, tzinfo=None).isoformat(timespec="minutes")
    payload = "\x1f".join(
        [
            DEDUP_VERSION,
            account_ref.strip().lower(),
            stamp,
            str(amount_minor),
            currency.upper(),
            normalize_description(description),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).digest()
