from __future__ import annotations

import os
import time
import sqlite3
from pathlib import Path
from collections import defaultdict, deque
from typing import Any
import json

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Humanity AI"
    model_base_url: str = "http://localhost:11434/v1"
    model_name: str = "qwen3:4b"
    model_api_key: str = ""
    store_conversations: bool = False
    database_path: str = "./data/humanity_ai.db"
    rate_limit_per_minute: int = 20
    max_message_chars: int = 12000
    max_request_bytes: int = 256_000
    max_history_messages: int = 16
    app_api_key: str = ""
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)


settings = Settings()
app = FastAPI(title=settings.app_name, version="0.4.0")
_hits: dict[str, deque[float]] = defaultdict(deque)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    history: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("message")
    @classmethod
    def trim_message(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Message cannot be empty")
        if len(value) > settings.max_message_chars:
            raise ValueError(f"Message exceeds {settings.max_message_chars} characters")
        return value


def db_connect() -> sqlite3.Connection:
    path = Path(settings.database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE IF NOT EXISTS conversations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        user_message TEXT NOT NULL,
        assistant_message TEXT NOT NULL
    )""")
    return conn


def enforce_rate_limit(request: Request) -> None:
    # Basic single-process guard only; use a shared limiter for multi-worker production.
    client = request.client.host if request.client else "unknown"
    now = time.time()
    bucket = _hits[client]
    while bucket and now - bucket[0] > 60:
        bucket.popleft()
    if len(bucket) >= settings.rate_limit_per_minute:
        raise HTTPException(status_code=429, detail="Rate limit reached. Please try again shortly.")
    bucket.append(now)
    # Prevent the in-memory limiter map from growing forever on a long-running instance.
    if len(_hits) > 10_000:
        stale = [key for key, values in _hits.items() if not values or now - values[-1] > 60]
        for key in stale:
            _hits.pop(key, None)


async def model_chat(messages: list[dict[str, str]]) -> str:
    headers = {"Authorization": f"Bearer {settings.model_api_key}"} if settings.model_api_key else {}
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                f"{settings.model_base_url.rstrip('/')}/chat/completions",
                headers=headers,
                json={
                    "model": settings.model_name,
                    "messages": messages,
                    "temperature": 0.6,
                },
            )
            response.raise_for_status()
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise ValueError("Model response content was not text")
            return content
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"Model server returned HTTP {exc.response.status_code}") from exc
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="Could not get a valid response from the configured model server") from exc


def clean_history(history: list[dict[str, Any]]) -> list[dict[str, str]]:
    cleaned: list[dict[str, str]] = []
    for item in history[-settings.max_history_messages:]:
        role = item.get("role")
        content = item.get("content")
        if role in ("user", "assistant") and isinstance(content, str):
            cleaned.append({"role": role, "content": content[:settings.max_message_chars]})
    return cleaned


@app.middleware("http")
async def enforce_body_size(request: Request, call_next):
    # Enforce the limit on actual bytes, not just a client-supplied Content-Length.
    if request.method in {"POST", "PUT", "PATCH"}:
        declared = request.headers.get("content-length")
        if declared:
            try:
                if int(declared) > settings.max_request_bytes:
                    return HTMLResponse("Request body is too large", status_code=413)
            except ValueError:
                return HTMLResponse("Invalid Content-Length header", status_code=400)
        body = await request.body()
        if len(body) > settings.max_request_bytes:
            return HTMLResponse("Request body is too large", status_code=413)
    if settings.app_api_key and request.url.path in {"/api/chat", "/v1/chat/completions"}:
        supplied = request.headers.get("authorization", "")
        if supplied != f"Bearer {settings.app_api_key}":
            return HTMLResponse("Unauthorized", status_code=401)
    return await call_next(request)


@app.get("/", response_class=HTMLResponse)
async def home():
    return Path(__file__).with_name("index.html").read_text(encoding="utf-8")


@app.get("/health")
async def health():
    return {"status": "ok", "app": settings.app_name, "version": "0.4.0"}


@app.get("/ready")
async def ready():
    try:
        async with httpx.AsyncClient(timeout=4) as client:
            r = await client.get(settings.model_base_url.rstrip("/") + "/models",
                                 headers={"Authorization": f"Bearer {settings.model_api_key}"} if settings.model_api_key else {})
            r.raise_for_status()
        return {"status": "ready", "model": settings.model_name}
    except httpx.HTTPError:
        raise HTTPException(status_code=503, detail="Model endpoint is not reachable")


@app.post("/api/chat")
async def chat(req: ChatRequest, request: Request):
    enforce_rate_limit(request)
    messages = [
        {"role": "system", "content": (
            "You are Humanity AI, a helpful, honest and respectful assistant. "
            "Be clear about uncertainty. Do not claim to have performed actions you have not performed. "
            "Respect user privacy and avoid harmful instructions."
        )},
        *clean_history(req.history),
        {"role": "user", "content": req.message},
    ]
    answer = await model_chat(messages)
    if settings.store_conversations:
        with db_connect() as conn:
            conn.execute(
                "INSERT INTO conversations(user_message, assistant_message) VALUES (?, ?)",
                (req.message, answer),
            )
    return {"answer": answer}


@app.post("/v1/chat/completions")
async def openai_compatible(req: Request):
    enforce_rate_limit(req)
    try:
        body = await req.json()
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="Request body must be valid JSON")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Request body must be a JSON object")
    messages = body.get("messages", [])
    if not isinstance(messages, list) or not messages:
        raise HTTPException(status_code=400, detail="A non-empty messages array is required")
    if len(messages) > settings.max_history_messages + 2:
        raise HTTPException(status_code=400, detail="Too many messages in request")
    safe_messages = []
    total_chars = 0
    for item in messages:
        if not isinstance(item, dict) or item.get("role") not in ("system", "user", "assistant") or not isinstance(item.get("content"), str):
            raise HTTPException(status_code=400, detail="Each message must have a supported role and string content")
        content = item["content"]
        total_chars += len(content)
        if len(content) > settings.max_message_chars or total_chars > settings.max_request_bytes:
            raise HTTPException(status_code=413, detail="Message content is too large")
        safe_messages.append({"role": item["role"], "content": content})
    answer = await model_chat(safe_messages)
    return {
        "id": "humanity-ai-local",
        "object": "chat.completion",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": answer}, "finish_reason": "stop"}],
    }
