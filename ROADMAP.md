# Kopiyka — 12-тижневий план

Принцип: щотижня є demoable slice, а не «накопичений код».

| Тиждень | Ціль | Ключовий deliverable |
|---:|---|---|
| **1** | Парсери | CLI `kopiyka-parse` для mono+privat, golden-тести, детекція кодування, зелений CI |
| 2 | БД + ідемпотентний імпорт | Alembic, `dedup_hash`, повторний залив → 0 дублікатів, матчинг internal transfers |
| 3 | Multi-tenancy foundation | households/memberships, RLS, tenant isolation suite |
| 4 | API + Cloudflare Access | FastAPI CRUD, upsert user з CF JWT, audit_log, export/delete |
| 5 | Категоризація | MCC-довідник, rule engine, ручні override |
| 6 | Frontend | Static SPA на Cloudflare Pages, базові дашборди |
| 7 | Власний auth | Google OIDC + magic link, міграція з CF Access |
| 8 | Deploy на VM | Caddy, Cloudflare Tunnel, `pg_dump` → R2, **тест відновлення в CI** |
| 9 | AWS-трек | OpenTofu: ECS + RDS + ALB, GitHub Actions OIDC, `tofu destroy` після сесії |
| 10 | Portability proof | Другий провайдер-модуль з тими самими outputs, час розгортання в README |
| 11 | Observability | Prometheus / Grafana, алерти на failed imports і 5xx |
| 12 | Аналітика та цілі | Планування крупної покупки, аналіз за категоріями |

Тижні 3 і 10 — те, що відрізняє цей проєкт від типового expense tracker.
Не скорочувати.
