# DropGrid

Photo Engine v1 automatically selects photos for prepared campaigns. It uses the
official Pixabay API, persistent 24-hour metadata caching, bounded downloads,
image normalization/deduplication and local MediaAsset storage. Configure the
backend-only `PIXABAY_API_KEY` in ignored `backend/.env`, Prepare a campaign and
click «Подобрать фото». Without a key, the app starts and the planner returns a
safe partial result using the existing library.

See [Photo Engine](docs/photo-engine.md) for policy, API, provider/license
requirements and tests. Docker's `media_data` volume survives ordinary `down`;
`down -v` deletes it intentionally. A complete backup requires both DB and media.

[Community Visual References](docs/community-visual-references.md) adds bounded
read-only VK style analysis and optional CPU similarity ranking. Community details
provide a manual profile, reference thumbnails and category/community preview.
[VK Archive Photo Provider](docs/vk-archive-photos.md) adds explicit same-community
archive opt-in, age-based indexing, lazy candidates and community cooldown.

DropGrid — фундамент системы управления кампаниями размещения контента в сообществах VK.
Сейчас это локальный development stack без автоматической отправки кампаний и без
аутентификации DropGrid. VK write diagnostics выключены по умолчанию.

## Implemented

- FastAPI `/api/v1`: аккаунты, сообщества, сетки и кампании; OpenAPI `/docs`.
- PostgreSQL, SQLAlchemy 2 async/asyncpg, UUID, UTC timestamps, Alembic.
- Чистые normalization/parser с построчными ошибками; preview и импорт сеток.
- Идемпотентный prepare, создающий Submission без отправки контента.
- Read-only Publication Monitor в worker и Telegram `/start`, `/help`, `/status`.
- React/TypeScript workflow: Dashboard с counts, импорт/preview сеток, черновики,
  Prepare, статистика и submissions с фильтрами/пагинацией; Accounts, Communities, Media.
- Docker Compose, тесты на настоящем PostgreSQL, Ruff, strict mypy, pre-commit, CI.
- Async VK user-token client, read operations, typed attachments/audio parser,
  isolated wall photo upload и guarded single-target diagnostic CLI.
- Account validation и Community resolve endpoints с injectable TokenProvider.
- Publication reconciliation: notifications → getById → canonical-photo fallback,
  список принятых постов и ручная проверка. [Monitor details](docs/publication-monitor.md).

## Planned

Официальный OAuth, разрешённая отправка кампаний и управление кампаниями через Telegram.

## Architecture

Web UI → FastAPI routers → services → SQLAlchemy/PostgreSQL.
Telegram проверяет API и БД. Worker выполняет read-only reconciliation отправленных предложек.
Domain parser не зависит от БД или сети. Подробнее: [architecture](docs/architecture.md),
[security](docs/security.md).

## Prerequisites

Docker Engine + Compose v2 (актуальная версия), либо Python 3.12+, PostgreSQL 16+,
Node.js 20.19+ / 22+ и npm. Для воспроизводимого backend setup используется
[uv](https://docs.astral.sh/uv/), зависимости закреплены в `backend/uv.lock`.

## Quick start: Docker

```sh
cp .env.example .env
docker compose up --build
```

Compose работает и без `.env`, используя development defaults. Поднимаются postgres,
одноразовый migrate, api, worker, frontend. API/worker ждут успешную миграцию;
frontend ждёт healthy API. БД сохраняется в named volume.

- Web: http://localhost:5173
- API docs: http://localhost:8000/docs
- Health: http://localhost:8000/health (200/503, включая доступность PostgreSQL)

Порты 5432, 8000, 5173 должны быть свободны. Если они заняты, задайте
`POSTGRES_PORT`, `BACKEND_PORT`, `FRONTEND_PORT` в `.env` и согласованно измените
`DATABASE_URL`, `BACKEND_URL`, `FRONTEND_ORIGIN`, `VITE_BACKEND_URL`.
Compose использует внутренний URL БД с hostname `postgres`; `.env DATABASE_URL`
предназначен для host-side процессов. `VITE_BACKEND_URL` используется при build:
после изменения пересоберите frontend.

```sh
docker compose logs -f api worker
docker compose down
```

`down` сохраняет данные. `down -v` удалит volume и все данные — используйте только
для намеренного сброса локального окружения.

## Local backend and migrations

```sh
docker compose up -d postgres
cd backend
uv sync --frozen --extra dev --python 3.12
cp ../.env.example .env  # ignored; change DATABASE_URL if necessary
uv run alembic upgrade head
uv run python -m dropgrid.api
```

Вместо uv можно создать venv и выполнить `pip install -e '.[dev]'`.
Команды Alembic запускаются из `backend`; Settings читает `.env` текущего каталога.
Миграции не запускаются неявно в API: Compose использует отдельный migrate service.

```sh
uv run alembic revision --autogenerate -m "describe change"
uv run alembic check
uv run python -m dropgrid.worker
```

Worker запускает цикл Publication Monitor каждые `WORKER_POLL_SECONDS`, учитывая
per-Account/per-Submission schedule и PostgreSQL leases. На SIGTERM/SIGINT закрывает
клиент и engine. Campaign sending не реализован.

## Telegram

Задайте `TELEGRAM_BOT_TOKEN` в локальном `.env`, затем:

```sh
docker compose --profile bot up --build
# or from backend:
uv run python -m dropgrid.bot
```

Без токена bot завершается с понятным сообщением; основной stack от него не зависит.
`BACKEND_URL` — URL для Telegram health check (Compose задаёт `http://api:8000`).
API не принимает VK tokens. Development diagnostics используют отдельные env
параметры; см. [VK integration](docs/vk-integration.md).

## Local frontend

```sh
cd frontend
npm ci
VITE_BACKEND_URL=http://localhost:8000 npm run dev
```

Также можно использовать ignored `frontend/.env.local`. API CORS разрешает ровно
`FRONTEND_ORIGIN` (по умолчанию http://localhost:5173).

## API example

```sh
curl -X POST http://localhost:8000/api/v1/grids/parse \
  -H 'Content-Type: application/json' \
  -d '{"text":"ГРУЗОВИКИ\n230 vk.com/example\n231 club123"}'
curl -X POST http://localhost:8000/api/v1/grids/import \
  -H 'Content-Type: application/json' \
  -d '{"name":"Demo","text":"ГРУЗОВИКИ\n230 vk.com/example\n231 club123"}'
```

`POST /grids` создаёт пустую сетку; `POST /grids/import` создаёт новую сетку,
переиспользует Community и возвращает grid + preview/errors. Валидные строки
импортируются несмотря на ошибки других строк; если валидных нет — 422 без записи.
GET `/grids/{id}` включает ограниченный список сообществ и summary категорий;
`GET /grids/{id}/communities` даёт пагинацию с категориями GridCommunity.
Полный browser workflow: [docs/web-workflow.md](docs/web-workflow.md).
**Prepare does not contact VK.**

Создайте campaign через `POST /api/v1/campaigns` с name, grid_id, track_url;
`publication_check_hours` по умолчанию 72, меняется для конкретной кампании.
PATCH разрешён только для draft. `POST /campaigns/{id}/prepare` переводит draft в
ready и возвращает `created`/`total`; повторные и параллельные вызовы не дублируют
Submission и не стирают историю. Пустая сетка даёт ready с total=0.
Произвольная смена campaign status через API пока закрыта.
Все списки принимают `limit` (1–200, default 100) и `offset`.

## Tests / lint / build

```sh
cd backend
uv run ruff check .
uv run ruff format --check .
uv run mypy src/dropgrid
uv run pytest -m 'not integration'
```

Integration tests требуют **отдельную** БД, мигрированную той же схемой. Fixture
очищает таблицы этой БД; никогда не указывайте рабочую БД.

```sh
docker compose exec postgres createdb -U dropgrid dropgrid_test
cd backend
DATABASE_URL=postgresql+asyncpg://dropgrid:dropgrid@localhost:5432/dropgrid_test uv run alembic upgrade head
TEST_DATABASE_URL=postgresql+asyncpg://dropgrid:dropgrid@localhost:5432/dropgrid_test uv run pytest
```

Без `TEST_DATABASE_URL` интеграционные тесты явно skipped. CI задаёт изолированную
БД и запускает весь suite после миграции и `alembic check`.

```sh
cd frontend
npm ci
npm run typecheck
npm run test
npm run build
# from backend (installs repository-wide Git hooks):
uv run pre-commit install
uv run pre-commit run --all-files
```

## VK integration / diagnostics

Полный researched contract, ограничения и примеры:
[docs/vk-integration.md](docs/vk-integration.md). VK_API_VERSION=5.199;
VK_WRITE_ENABLED=false. Для read-only diagnostics нужен user token через ignored
environment и точная привязка VK_TEST_ACCOUNT_ID к одному UUID. Production token
storage пока отсутствует: encrypted_access_token не считается plaintext.

Новые read-only VK endpoints:
`POST /api/v1/accounts/{id}/validate`,
`POST /api/v1/communities/{id}/resolve` (body: account_id UUID).
Без безопасно настроенного provider возвращают 503; disabled account — 409.
Невалидный token сохраняет Account.invalid и возвращает valid=false.

```sh
# from backend, after configuring private environment:
uv run python -m dropgrid.integrations.vk.diagnostics account
uv run python -m dropgrid.integrations.vk.diagnostics community example
uv run python -m dropgrid.integrations.vk.diagnostics wall example
```

Никаких tokens в CLI arguments. Live suggest требует write flag **и** точный
allowlisted target; может создать реальную публикацию вместо suggestion в зависимости
от прав/настроек. Worker выполняет только read-only monitoring. Обычные тесты блокируют
реальные HTTP requests; VK tests используют MockTransport.

## Configuration

См. `.env.example`: секреты только через environment / ignored `.env`.
`APP_SECRET_KEY` — Fernet key для encrypted Account tokens; worker и API должны
использовать один ключ. Development пароль PostgreSQL из Compose — публичный локальный
default, а не production credential. Порты опубликованы только на loopback.

## Structure

```text
backend/
  src/dropgrid/{api,domain,services,db,bot,worker,integrations/vk}
  tests/                 unit + PostgreSQL integration
  alembic/versions/      explicit schema migrations
  pyproject.toml, uv.lock
frontend/src/            React pages + typed API helper
docker/                  Dockerfiles + nginx SPA config
docker-compose.yml       local stack + optional bot profile
docs/                    architecture, security
.github/workflows/ci.yml  backend + frontend checks
```

Photo retrieval context: [Grid comments and content hints](docs/photo-content-hints.md).
