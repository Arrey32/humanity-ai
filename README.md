# Humanity AI

An open, self-hostable assistant prototype designed around accessibility, transparent operation, and privacy-conscious defaults.

## Current release

**v0.6.0 — local deployment candidate.** The automated test suite covers health checks, chat request validation, authentication, account deletion, per-account conversation privacy, and PWA assets. It does not replace a real model-inference test, Docker test, penetration test, or provider deployment.

## Fastest local setup (Windows 10/11, macOS, Linux)

### Option A: Docker Compose with a local Ollama model

1. Install Docker Desktop (Windows/macOS) or Docker Engine + Compose (Linux).
2. Extract this ZIP into a folder.
3. Open PowerShell/Terminal in that folder.
4. Copy `.env.docker.example` to `.env`.
5. Generate a strong secret and put it in `.env` as `AUTH_SECRET`:

   ```powershell
   python -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

6. Start the application:

   ```bash
   docker compose up --build -d
   ```

   The compose setup starts Ollama, downloads `qwen3:4b` into a persistent Docker volume, then starts Humanity AI. The first startup can take a while because the model download is several gigabytes.

7. Open <http://localhost:8000> and create an account. Chat is configured to require sign-in by default.
8. Check `http://localhost:8000/health` and `http://localhost:8000/ready`.
9. Stop it with `docker compose down`. Your model and application data remain in named volumes. To erase them too, use `docker compose down -v` (this permanently deletes local data).

If your computer has limited memory, change `MODEL_NAME` to a smaller Ollama model that your hardware can run. Model names, memory needs, and licenses vary; verify the model's license before redistribution or public hosting. CPU inference may be slow. No GPU acceleration is configured by default.

### Option B: Connect to a hosted OpenAI-compatible model endpoint

Set `MODEL_BASE_URL`, `MODEL_NAME`, and `MODEL_API_KEY` in `.env` to the endpoint and credentials supplied by your model provider. Keep `AUTH_REQUIRED=true` and set a unique `AUTH_SECRET`. Do not commit `.env` or share API keys.

## Features

- Browser chat UI and OpenAI-compatible `/v1/chat/completions` endpoint.
- Ollama-compatible model integration.
- Account registration, login, sign-out, and account deletion.
- Passwords hashed with scrypt; signed expiring bearer tokens.
- Optional per-user saved conversations; disabled by default.
- Per-user conversation list and deletion endpoints.
- Request-size limits and basic per-process rate limiting.
- Installable Progressive Web App (PWA) for supported mobile and desktop browsers.
- Docker Compose local stack with persistent model and application volumes.

## Privacy defaults and controls

- `STORE_CONVERSATIONS=false` by default. When enabled, chat messages are saved only for signed-in users and are isolated by user ID.
- `DELETE /api/conversations` deletes the signed-in user's saved chats.
- `DELETE /api/auth/me` deletes the account and its saved chats. Existing tokens stop working because each request checks that the account still exists.
- The app does not provide email verification, password reset, MFA, or a user-facing data-export feature yet. Do not use it for sensitive or regulated data.
- The in-memory rate limiter is per process and is not adequate as the sole protection for a public multi-instance service.

## Mobile

The PWA can be installed from the browser's install/add-to-home-screen menu when served over HTTPS (or localhost). It is a mobile-friendly web app, not a native Android APK or iOS App Store package. Native store distribution requires platform build tools, signing keys, developer accounts, device testing, and store review.

## API quick reference

- `GET /health` — application health.
- `GET /ready` — checks whether the configured model endpoint is reachable.
- `POST /api/auth/register` and `POST /api/auth/login` — create account/sign in.
- `GET /api/auth/me` — current account.
- `DELETE /api/auth/me` — delete account and saved conversations.
- `POST /api/chat` — chat request `{ "message": "Hello", "history": [] }`.
- `GET /api/conversations` — list saved conversations for the signed-in user (only when storage is enabled).
- `DELETE /api/conversations` — delete the signed-in user's saved conversations.
- `POST /v1/chat/completions` — OpenAI-compatible chat endpoint.

## Development and tests

```bash
python -m venv .venv
# Activate the environment, then:
pip install -r requirements.txt
python -m pytest -q
python -m compileall -q app tests
```

## Before public launch

A public launch still requires: an independently reviewed security design; distributed rate limiting and abuse controls; HTTPS/domain configuration; database backups and restore drills; monitoring and incident response; privacy/terms documentation; account verification and recovery; load and model-quality evaluation; model-license review; and a real deployment test. Hosting and model-provider costs may apply. The software is not currently hosted by this project.
