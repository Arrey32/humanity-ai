# Deployment notes

## Local Docker deployment

Use the Docker Compose instructions in `README.md`. The compose stack is configured to:

1. Start Ollama with a persistent model volume.
2. Wait for Ollama to respond.
3. Pull the selected model (`MODEL_NAME`, default `qwen3:4b`).
4. Start Humanity AI with authentication enabled and persistent SQLite storage.

The API container runs as a non-root user and has a health check. Docker Compose syntax was parsed during development, but an actual Docker build and container run were not possible in the development environment because Docker was not installed. Verify the full stack on the target computer before relying on it.

## Render

`render.yaml` is a deployment template, not a completed deployment. Create a Render web service from the repository, review the selected plan and persistent-disk charges, and set `MODEL_BASE_URL`, `MODEL_NAME`, and optional `MODEL_API_KEY` to a reachable model endpoint. The generated `AUTH_SECRET` must remain private. Confirm that the chosen Render plan supports the disk and resource requirements. The included template does not automatically provision an Ollama model server.

## Fly.io

`fly.toml` is a starting template only. Choose a unique app name, create the app and persistent volume, configure secrets (`AUTH_SECRET`, `MODEL_BASE_URL`, `MODEL_NAME`, and optionally `MODEL_API_KEY`), then deploy. Confirm region, machine sizing, volume backups, and billing first. Do not expose a local-only Ollama URL to a remote app.

## Required production work

Do not launch publicly until the app has a security review, distributed rate limiting, abuse reporting/moderation policy, backup/restore procedures, observability, privacy/terms notices, and tested account recovery. SQLite on a single persistent disk is suitable only for a small single-instance prototype; use managed PostgreSQL and migrations for a larger service. Never commit `.env`, passwords, signing secrets, or model-provider keys.
