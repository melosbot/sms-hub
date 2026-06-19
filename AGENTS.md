# Repository Guidelines

## Project Structure & Module Organization

This repository is an SMS gateway built around a thin ESP32C3 device and a Python Hub. Hub code lives in `core/`: `main.py` starts FastAPI, `app/routes/` contains HTTP routes, `infra/` owns config, SQLite, and events, `device/` handles device I/O, `sms/` handles rules and outbound sending, and `notify/` handles delivery. The Web UI is a separate Vite + React project in `web/` (its `dist/` build is served by the Hub). Firmware is in `firmware/`. Tests are under `test/unit/`, while `test/demo/` contains the mock device and demo stack. Design, API, and deployment notes live in `docs/`.

## Build, Test, and Development Commands

- `python3 -m venv .local/venv && . .local/venv/bin/activate`: create and enter the local virtualenv.
- `pip install -r core/requirements.txt -r core/requirements-dev.txt`: install runtime and test dependencies.
- `test/demo/demo start`: run Hub plus mock device for UI and integration work. Use `stop`, `restart`, `status`, `logs`, or `paths`.
- `DATA_DIR=/tmp/sms-hub-dev DEVICE_TOKEN=test-token-local DEVICE_URL=http://127.0.0.1:8888/test-token-local WEBUI_PASS=test123 python -m core.main`: run the Hub manually.
- `.local/venv/bin/python -m pytest -q test/unit`: run the unit test suite.
- `docker compose up -d --build`: build and run the production-style container.

## Coding Style & Naming Conventions

Use Python 3.12-compatible code with 4-space indentation, useful module docstrings, and snake_case names for modules, functions, variables, and tests. Keep route modules grouped by API surface in `core/app/routes/`. Prefer existing helpers such as `phone.canonicalize()` and `device._json_body()` over duplicated behavior. The Web UI is built with Vite (`npm run build` in `web/`); follow `web/DESIGN.md` before editing UI components.

## Testing Guidelines

Tests use `pytest` and mock external device behavior. Name new files `test_<area>.py` and test functions `test_<behavior>`. For database work, isolate state with temporary paths and add migration coverage when schema changes. Changes to `poller`, `device`, `sender`, or `notifier` should include focused unit tests and, when hardware behavior changes, a manual regression pass from `docs/operations.md`.

## Commit & Pull Request Guidelines

Recent history uses Conventional Commit-style prefixes such as `feat:`, `refactor:`, and `docs:`. Keep subjects imperative and scoped, for example `feat: add retry status endpoint`. Pull requests should describe behavior changes, list tests run, link issues, and include screenshots for Web UI changes. Call out config, data migration, firmware, or Docker impacts explicitly.

## Security & Configuration Tips

Do not commit `.env`, `firmware/config.h`, database files, demo run data, or secrets. Copy from `.env.example` and `firmware/config.example.h`. Always set `DATA_DIR` for manual Hub runs, and treat this project as LAN-only unless the security model is intentionally redesigned.
