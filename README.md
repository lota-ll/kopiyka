# Kopiyka

Мультитенантна аналітика особистих витрат на основі банківських виписок
(monobank, Приват24). Без підключення до банківського API — достатньо
завантажити CSV-файл.

> **Статус:** MVP у розробленні. Тиждень 1 з 12 (див. [ROADMAP.md](ROADMAP.md)).

## Чому цей проєкт існує

Два одночасні завдання:

1. **Реальна потреба.** Автор користується застосунком щодня — тому
   зламаний ingestion помічається того ж вечора, а не через місяць.
2. **DevOps/DevSecOps-полігон.** Проєкт свідомо збудовано так, щоб
   пройти повний життєвий цикл: контейнеризація, IaC, CI/CD з
   supply-chain-безпекою, observability, backup з перевіркою відновлення
   та розгортання щонайменше у двох незалежних хмарах.

Оскільки система обробляє **чужі фінансові дані**, ізоляція тенантів тут
не декларація, а тестований інваріант — див. [SECURITY.md](SECURITY.md).

## Швидкий старт

Потрібні: Docker 24+, Docker Compose v2, `make`, Python 3.12 (для запуску
CLI поза контейнером).

```bash
git clone https://github.com/USER/kopiyka.git
cd kopiyka
cp .env.example .env

make up        # Postgres + MinIO + API
make migrate   # схема + RLS-політики
make seed      # глобальні категорії та демо-household

open http://localhost:8000/docs
```

Розбір виписки без БД і без Docker:

```bash
pip install -e ".[dev]"
make parse FILE=~/Downloads/statement.csv
```

Зупинити:

```bash
make down   # дані лишаються
make nuke   # разом з даними
```

## Архітектура

```
                    ┌──────────────────────┐
   файл ──────────► │  loaders/            │  «як прочитати»
   (.csv / .xlsx)   │  csv_loader xlsx_... │  формат, кодування, типи Excel
                    └──────────┬───────────┘
                               │ rows: list[list[str]]
                    ┌──────────▼───────────┐
                    │  parsers/            │  «як зрозуміти колонки»
                    │  mono.py privat.py   │  єдина точка знання про банк
                    └──────────┬───────────┘
                               │ ParsedTransaction (канонічна форма)
                    ┌──────────▼───────────┐
                    │  domain/             │  money: BIGINT копійок
                    │  money.py dedup.py   │  dedup: SHA-256 ключ
                    └──────────┬───────────┘
                               │
   HTTP ──► FastAPI ──► tenant_session(household_id)
                               │  SET LOCAL app.household_id
                    ┌──────────▼───────────┐
                    │  PostgreSQL 16       │
                    │  Row Level Security  │  ◄── другий рівень ізоляції
                    └──────────────────────┘
```

**Правило шарів:** банк-специфічний код живе тільки у `parsers/`. Додати
новий банк = один файл + `@register`. Нічого іншого в проєкті не змінюється.

## Структура репозиторію

| Шлях | Призначення |
|---|---|
| `src/kopiyka/loaders/` | Читання CSV/XLSX, детекція кодування, типи Excel |
| `src/kopiyka/parsers/` | Адаптери банків, мапи колонок, `account_hint` |
| `src/kopiyka/domain/` | Гроші та дедуплікація — чиста логіка, без БД |
| `src/kopiyka/db/` | ORM-моделі, сесії з tenant-контекстом |
| `src/kopiyka/api/` | FastAPI: auth, роутери, схеми |
| `src/kopiyka/categorize/` | MCC-мапа, правила категоризації |
| `migrations/` | Alembic; `0002` містить RLS-політики |
| `tests/` | Golden-фікстури, ідемпотентність, tenant isolation suite |
| `docker/`, `.github/` | Локальний стек і CI/CD |
| `docs/adr/` | Architecture Decision Records |

## Ключові технічні рішення

| Рішення | Чому |
|---|---|
| Гроші як `BIGINT` у копійках | `float` для грошей — дефект за визначенням |
| `dedup_hash` замість `id` банку | CSV-експорт не має ідентифікаторів; імпорт має бути ідемпотентним |
| PostgreSQL RLS + `FORCE` | Один забутий `WHERE` не має перетворюватися на витік чужої виписки |
| Household ≠ user | Спільний бюджет — очевидна наступна фіча; додати тенанта потім = переписати всі запити |
| `loaders/` окремо від `parsers/` | Формат файлу і банк — незалежні осі (ADR-0006) |
| `account_hint` на рівні рядка | Виписка Приват24 містить кілька карток в одному файлі |
| S3 через `endpoint_url` | Той самий код працює з S3, R2, MinIO, B2 |
| Тільки env-змінні у config | Основа портабельності між провайдерами |
| Без паролів (SSO + magic link) | Немає паролів — немає credential stuffing (ADR-0004) |

Детальні обґрунтування — у [`docs/adr/`](docs/adr/).

## Тести

```bash
make test      # без БД: парсери, гроші, дедуплікація, інвентаризація маршрутів
make test-db   # + RLS та cross-tenant перевірки (потрібен піднятий стек)
```

`tests/test_tenant_isolation.py` містить `ROUTE_MATRIX`: новий маршрут,
не описаний у ній, автоматично валить CI. Забути про ізоляцію неможливо.

## Ліцензія

MIT
