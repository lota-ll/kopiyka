"""Виявлення внутрішніх переказів між власними рахунками.

Навіщо це піднято з тижня 5 у тиждень 2: за чотирьох карток (дві monobank,
дві Приват24) перекидання грошей між ними — щоденна операція. Кожен такий
переказ потрапляє у виписки **двічі**: як витрата на одному рахунку і як
надходження на іншому.

Без фільтрації «загальна картина витрат» буде роздута рівно на суму всіх
внутрішніх переказів, і графіки брехатимуть правдоподібно — найгірший тип
помилки в аналітиці.

Евристика матчингу:
  * протилежні знаки;
  * однакова сума за модулем (у валюті рахунку);
  * різні рахунки одного household;
  * різниця в часі менша за вікно (типово 24 год).

Кожна транзакція може бути зіставлена лише один раз — це не дає одному
надходженню «погасити» кілька витрат.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

DEFAULT_WINDOW = timedelta(hours=24)


@dataclass(slots=True, frozen=True)
class TransferCandidate:
    """Пара транзакцій, що виглядає як внутрішній переказ."""

    outgoing_id: object
    incoming_id: object
    amount_minor: int
    delta: timedelta


@dataclass(slots=True)
class TransferRow:
    """Мінімальний зріз транзакції, потрібний для матчингу."""

    id: object
    account_id: object
    booked_at: datetime
    amount_account_minor: int


def match_internal_transfers(
    rows: list[TransferRow],
    *,
    window: timedelta = DEFAULT_WINDOW,
) -> list[TransferCandidate]:
    """Знаходить пари внутрішніх переказів.

    Складність — O(n·k), де k — кількість кандидатів з тією ж сумою.
    На побутових обсягах (тисячі транзакцій) цього достатньо; якщо стане
    вузьким місцем, матчинг переїде в SQL.
    """
    outgoing = sorted((r for r in rows if r.amount_account_minor < 0), key=lambda r: r.booked_at)
    # Індекс надходжень за абсолютною сумою — щоб не робити повний перебір.
    incoming_by_amount: dict[int, list[TransferRow]] = {}
    for row in rows:
        if row.amount_account_minor > 0:
            incoming_by_amount.setdefault(row.amount_account_minor, []).append(row)
    for bucket in incoming_by_amount.values():
        bucket.sort(key=lambda r: r.booked_at)

    used: set[object] = set()
    pairs: list[TransferCandidate] = []

    for out in outgoing:
        if out.id in used:
            continue
        amount = abs(out.amount_account_minor)
        best: TransferRow | None = None
        best_delta = window

        for candidate in incoming_by_amount.get(amount, ()):
            if candidate.id in used or candidate.account_id == out.account_id:
                continue
            delta = abs(candidate.booked_at - out.booked_at)
            if delta <= best_delta:
                best, best_delta = candidate, delta

        if best is not None:
            used.add(out.id)
            used.add(best.id)
            pairs.append(
                TransferCandidate(
                    outgoing_id=out.id,
                    incoming_id=best.id,
                    amount_minor=amount,
                    delta=best_delta,
                )
            )

    return pairs
