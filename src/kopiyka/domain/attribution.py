"""Визначення рахунку для операцій без номера картки.

Проблема, виявлена на реальних виписках Приват24: частина операцій не має
номера картки — зокрема «Списання відсотків за використання кредиту».
Банк не вважає їх картковими, бо це рух за кредитним договором.

Наївне рішення — віднести такі рядки до рахунку за замовчуванням — псує
дані двома способами: сума потрапляє не на той рахунок, і ланцюг залишків
справжнього рахунку розривається.

Правильне рішення спирається на властивість самих даних: **залишок після
операції утворює неперервний ланцюг у межах рахунку**. Отже, безкарткову
операцію можна віднести до того рахунку, чий ланцюг вона робить цілим:

    залишок(операції) == залишок(попередньої) + сума(операції)
    залишок(наступної) - сума(наступної) == залишок(операції)

Якщо рівності виконуються рівно для одного рахунку — атрибуція однозначна.
Якщо для кількох або для жодного — операція лишається невизначеною, і це
чесно повідомляється, а не приховується вгадуванням.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from kopiyka.parsers.base import ParsedTransaction


@dataclass(slots=True)
class AttributionReport:
    """Результат атрибуції."""

    resolved: dict[int, str] = field(default_factory=dict)  # source_row → account_ref
    unresolved: list[int] = field(default_factory=list)  # source_row
    ambiguous: dict[int, list[str]] = field(default_factory=dict)

    @property
    def warnings(self) -> list[str]:
        out: list[str] = []
        if self.resolved:
            pairs = ", ".join(f"рядок {row}→{acc}" for row, acc in sorted(self.resolved.items()))
            out.append(f"безкарткові операції віднесено за ланцюгом залишків: {pairs}")
        if self.ambiguous:
            for row, cands in sorted(self.ambiguous.items()):
                out.append(f"рядок {row}: ланцюг сходиться для кількох рахунків {cands}")
        if self.unresolved:
            out.append(
                f"рядків без картки, які не вдалося віднести: {sorted(self.unresolved)} "
                "— буде використано рахунок за замовчуванням"
            )
        return out


def _sort_key(tx: ParsedTransaction) -> tuple[object, int]:
    # Виписка йде від новіших до старіших, тому при однаковому часі
    # більший source_row означає ранішу операцію.
    return (tx.booked_at, -tx.source_row)


def attribute_orphans(transactions: list[ParsedTransaction]) -> AttributionReport:
    """Проставляє ``account_hint`` там, де його не було.

    Мутує переданий список. Повертає звіт про те, що вдалося визначити.
    """
    report = AttributionReport()

    chains: dict[str, list[ParsedTransaction]] = {}
    for tx in transactions:
        if tx.account_hint and tx.balance_after_minor is not None:
            chains.setdefault(tx.account_hint, []).append(tx)
    for rows in chains.values():
        rows.sort(key=_sort_key)

    orphans = [
        tx for tx in transactions if tx.account_hint is None and tx.balance_after_minor is not None
    ]
    # Обробляємо в хронологічному порядку: віднесена операція стає частиною
    # ланцюга і може допомогти визначити наступну.
    orphans.sort(key=_sort_key)

    for orphan in orphans:
        candidates = [ref for ref, rows in chains.items() if _fits(orphan, rows)]

        if len(candidates) == 1:
            ref = candidates[0]
            orphan.account_hint = ref
            chains[ref].append(orphan)
            chains[ref].sort(key=_sort_key)
            report.resolved[orphan.source_row] = ref
        elif len(candidates) > 1:
            report.ambiguous[orphan.source_row] = sorted(candidates)
        else:
            report.unresolved.append(orphan.source_row)

    # Рядки взагалі без залишку перевірити нічим.
    report.unresolved.extend(
        tx.source_row
        for tx in transactions
        if tx.account_hint is None and tx.balance_after_minor is None
    )
    return report


def _fits(orphan: ParsedTransaction, rows: list[ParsedTransaction]) -> bool:
    """Чи робить ця операція ланцюг рахунку цілим."""
    if not rows:
        return False

    key = _sort_key(orphan)
    predecessor: ParsedTransaction | None = None
    successor: ParsedTransaction | None = None
    for row in rows:
        if _sort_key(row) < key:
            predecessor = row
        elif successor is None:
            successor = row

    if predecessor is None and successor is None:
        return False

    assert orphan.balance_after_minor is not None

    if predecessor is not None:
        assert predecessor.balance_after_minor is not None
        if predecessor.balance_after_minor + orphan.amount_account_minor != (
            orphan.balance_after_minor
        ):
            return False

    if successor is not None:
        assert successor.balance_after_minor is not None
        if successor.balance_after_minor - successor.amount_account_minor != (
            orphan.balance_after_minor
        ):
            return False

    return True
