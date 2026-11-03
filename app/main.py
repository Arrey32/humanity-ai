from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time

from collections import defaultdict, deque
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# ---------------------------------------------------------
# Configuration
# ---------------------------------------------------------

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
    auth_required: bool = False
    auth_secret: str = ""
    auth_token_hours: int = 24

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
    )


settings = Settings()


# ---------------------------------------------------------
# Request-size middleware
# ---------------------------------------------------------

class RequestSizeLimitMiddleware:
    """
    Limits the size of incoming HTTP request bodies.
    Buffers only up to the configured maximum before passing
    the request to the application.
    """

    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in {
            "POST", "PUT", "PATCH"
        }:
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        content_length = headers.get(b"content-length")

        if content_length:
            try:
                if int(content_length) > self.max_bytes:
                    response = JSONResponse(
                        {"detail": "Request body is too large"},
                        status_code=413,
                    )
                    await response(scope, receive, send)
                    return
            except ValueError:
                response = JSONResponse(
                    {"detail": "Invalid Content-Length header"},
                    status_code=400,
                )
                await response(scope, receive, send)
                return

        body_parts = []
        total_size = 0

        while True:
            message = await receive()

            if message["type"] == "http.disconnect":
                return

            if message["type"] != "http.request":
                continue

            chunk = message.get("body", b"")
            total_size += len(chunk)

            if total_size > self.max_bytes:
                response = JSONResponse(
                    {"detail": "Request body is too large"},
                    status_code=413,
                )
                await response(scope, receive, send)
                return

            body_parts.append(chunk)

            if not message.get("more_body", False):
                break

        body = b"".join(body_parts)
        body_sent = False

        async def replay_receive():
            nonlocal body_sent

            if not body_sent:
                body_sent = True
                return {
                    "type": "http.request",
                    "body": body,
                    "more_body": False,
                }

            return {
                "type": "http.request",
                "body": b"",
                "more_body": False,
            }

        await self.app(scope, replay_receive, send)


# ---------------------------------------------------------
# Application setup
# ---------------------------------------------------------

app = FastAPI(
    title=settings.app_name,
    version="0.6.1",
)

app.add_middleware(
    RequestSizeLimitMiddleware,
    max_bytes=settings.max_request_bytes,
)

app.mount(
    "/static",
    StaticFiles(directory=Path(__file__).with_name("static")),
    name="static",
)

_hits: dict[str, deque[float]] = defaultdict(deque)


# ---------------------------------------------------------
# Database
# ---------------------------------------------------------

@contextmanager
def db_connect() -> Iterator[sqlite3.Connection]:
    """
    Opens a database connection, creates required tables,
    commits successful operations, and always closes it.
    """

    path = Path(settings.database_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(path, timeout=10)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")

    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                user_message TEXT NOT NULL,
                assistant_message TEXT NOT NULL
            )
        """)

        columns = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(conversations)"
            )
        }

        if "user_id" not in columns:
            conn.execute("""
                ALTER TABLE conversations
                ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE CASCADE
            """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_conversations_user_created
            ON conversations(user_id, created_at)
        """)

        yield conn
        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


# ---------------------------------------------------------
# Request models and validation
# ---------------------------------------------------------

class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    history: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        value = value.strip()

        if not value:
            raise ValueError("Message cannot be empty")

        if len(value) > settings.max_message_chars:
            raise ValueError(
                f"Message exceeds {settings.max_message_chars} characters"
            )

        return value


class AccountInput(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=12, max_length=128)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        value = value.strip().lower()

        pattern = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"

        if not re.match(pattern, value):
            raise ValueError("Enter a valid email address")

        return value


# ---------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------

def enforce_rate_limit(request: Request) -> None:
    """
    Basic in-memory, single-process rate limiter.
    A shared limiter is needed for a multi-instance deployment.
    """

    client = request.client.host if request.client else "unknown"
    now = time.time()

    bucket = _hits[client]

    while bucket and now - bucket[0] > 60:
        bucket.popleft()

    if len(bucket) >= settings.rate_limit_per_minute:
        raise HTTPException(
            status_code=429,
            detail="Rate limit reached. Please try again shortly.",
        )

    bucket.append(now)

    if len(_hits) > 10_000:
        stale = [
            key
            for key, values in _hits.items()
            if not values or now - values[-1] > 60
        ]

        for key in stale:
            _hits.pop(key, None)


# ---------------------------------------------------------
# Password hashing and authentication
# ---------------------------------------------------------

def encode_base64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def decode_base64(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def get_auth_secret() -> bytes:
    secret = settings.auth_secret or settings.app_api_key

    if not secret:
        raise HTTPException(
            status_code=503,
            detail="Set AUTH_SECRET before enabling account registration",
        )

    if settings.auth_required and len(secret) < 32:
        raise HTTPException(
            status_code=503,
            detail="AUTH_SECRET must contain at least 32 characters",
        )

    return secret.encode("utf-8")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)

    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=2**14,
        r=8,
        p=1,
    )

    return (
        f"scrypt${encode_base64(salt)}${encode_base64(digest)}"
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_text, digest_text = stored.split("$")

        if scheme != "scrypt":
            return False

        salt = decode_base64(salt_text)
        expected = decode_base64(digest_text)

        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=2**14,
            r=8,
            p=1,
        )

        return hmac.compare_digest(actual, expected)

    except (ValueError, TypeError, binascii.Error):
        return False


def issue_token(user_id: int) -> str:
    header = encode_base64(
        b'{"alg":"HS256","typ":"JWT"}'
    )

    payload_data = {
        "sub": str(user_id),
        "exp": int(time.time()) + settings.auth_token_hours * 3600,
    }

    payload = encode_base64(
        json.dumps(payload_data).encode("utf-8")
    )

    message = f"{header}.{payload}"

    signature = encode_base64(
        hmac.new(
            get_auth_secret(),
            message.encode("utf-8"),
            hashlib.sha256,
        ).digest()
    )

    return f"{message}.{signature}"


def authenticated_user(
    request: Request,
) -> dict[str, Any] | None:

    authorization = request.headers.get("authorization", "")

    if not authorization.startswith("Bearer "):
        if settings.auth_required or settings.app_api_key:
            raise HTTPException(
                status_code=401,
                detail="Sign in to use chat",
            )

        return None

    token = authorization[7:].strip()

    if not token:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired sign-in token",
        )

    if settings.app_api_key and hmac.compare_digest(
        token,
        settings.app_api_key,
    ):
        return None

    try:
        header, payload, signature = token.split(".")
        message = f"{header}.{payload}"

        expected_signature = encode_base64(
            hmac.new(
                get_auth_secret(),
                message.encode("utf-8"),
                hashlib.sha256,
            ).digest()
        )

        if not hmac.compare_digest(
            signature,
            expected_signature,
        ):
            raise ValueError("Invalid signature")

        token_header = json.loads(decode_base64(header))

        if token_header.get("alg") != "HS256":
            raise ValueError("Unsupported token algorithm")

        data = json.loads(decode_base64(payload))

        if int(data["exp"]) <= int(time.time()):
            raise ValueError("Token expired")

        user_id = int(data["sub"])

        with db_connect() as conn:
            row = conn.execute(
                """
                SELECT id, email, created_at
                FROM users
                WHERE id = ?
                """,
                (user_id,),
            ).fetchone()

        if not row:
            raise ValueError("Unknown user")

        return {
            "id": row[0],
            "email": row[1],
            "created_at": row[2],
        }

    except (
        ValueError,
        KeyError,
        TypeError,
        json.JSONDecodeError,
        binascii.Error,
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired sign-in token",
        )


# ---------------------------------------------------------
# Chat history and AI model connection
# ---------------------------------------------------------

def clean_history(
    history: list[dict[str, Any]],
) -> list[dict[str, str]]:

    cleaned = []

    for item in history[-settings.max_history_messages:]:
        role = item.get("role")
        content = item.get("content")

        if (
            role in ("user", "assistant")
            and isinstance(content, str)
        ):
            cleaned.append({
                "role": role,
                "content": content[:settings.max_message_chars],
            })

    return cleaned


async def model_chat(
    messages: list[dict[str, str]],
) -> str:

    headers = {}

    if settings.model_api_key:
        headers["Authorization"] = (
            f"Bearer {settings.model_api_key}"
        )

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
        raise HTTPException(
            status_code=502,
            detail=(
                "Model server returned HTTP "
                f"{exc.response.status_code}"
            ),
        ) from exc

    except (
        httpx.HTTPError,
        KeyError,
        IndexError,
        TypeError,
        ValueError,
    ) as exc:
        raise HTTPException(
            status_code=502,
            detail="Could not get a valid response from the configured model server",
        ) from exc


# ---------------------------------------------------------
# Basic application routes
# ---------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def home():
    return Path(__file__).with_name("index.html").read_text(
        encoding="utf-8"
    )


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": "0.6.1",
    }


@app.get("/ready")
async def ready():
    headers = {}

    if settings.model_api_key:
        headers["Authorization"] = (
            f"Bearer {settings.model_api_key}"
        )

    try:
        async with httpx.AsyncClient(timeout=4) as client:
            response = await client.get(
                settings.model_base_url.rstrip("/") + "/models",
                headers=headers,
            )

            response.raise_for_status()

        return {
            "status": "ready",
            "model": settings.model_name,
        }

    except httpx.HTTPError:
        raise HTTPException(
            status_code=503,
            detail="Model endpoint is not reachable",
        )


# ---------------------------------------------------------
# Account registration and login
# ---------------------------------------------------------

@app.post("/api/auth/register")
async def register(
    account: AccountInput,
    request: Request,
):
    enforce_rate_limit(request)

    with db_connect() as conn:
        try:
            cursor = conn.execute(
                """
                INSERT INTO users (email, password_hash)
                VALUES (?, ?)
                """,
                (
                    account.email,
                    hash_password(account.password),
                ),
            )

            user_id = cursor.lastrowid

        except sqlite3.IntegrityError:
            raise HTTPException(
                status_code=409,
                detail="An account with this email already exists",
            )

    return {
        "token": issue_token(user_id),
        "token_type": "bearer",
        "expires_in": settings.auth_token_hours * 3600,
    }


@app.post("/api/auth/login")
async def login(
    account: AccountInput,
    request: Request,
):
    enforce_rate_limit(request)

    with db_connect() as conn:
        row = conn.execute(
            """
            SELECT id, password_hash
            FROM users
            WHERE email = ?
            """,
            (account.email,),
        ).fetchone()

    if not row or not verify_password(account.password, row[1]):
        raise HTTPException(
            status_code=401,
            detail="Email or password is incorrect",
        )

    return {
        "token": issue_token(row[0]),
        "token_type": "bearer",
        "expires_in": settings.auth_token_hours * 3600,
    }


@app.get("/api/auth/me")
async def account_me(request: Request):
    user = authenticated_user(request)

    if not user:
        raise HTTPException(
            status_code=401,
            detail="Sign in first",
        )

    return user


@app.delete("/api/auth/me")
async def delete_account(request: Request):
    user = authenticated_user(request)

    if not user:
        raise HTTPException(
            status_code=401,
            detail="Sign in first",
        )

    with db_connect() as conn:
        conn.execute(
            "DELETE FROM conversations WHERE user_id = ?",
            (user["id"],),
        )

        conn.execute(
            "DELETE FROM users WHERE id = ?",
            (user["id"],),
        )

    return {"status": "deleted"}


# ---------------------------------------------------------
# Main chat endpoint
# ---------------------------------------------------------

@app.post("/api/chat")
async def chat(
    req: ChatRequest,
    request: Request,
):
    enforce_rate_limit(request)

    user = authenticated_user(request)

    messages = [
        {
            "role": "system",
            "content": (
                "You are Humanity AI, a helpful, honest and respectful assistant. "
                "Be clear about uncertainty. Do not claim to have performed actions "
                "you have not performed. Respect user privacy and avoid harmful instructions."
            ),
        },
        *clean_history(req.history),
        {
            "role": "user",
            "content": req.message,
        },
    ]

    answer = await model_chat(messages)

    if settings.store_conversations and user:
        with db_connect() as conn:
            conn.execute(
                """
                INSERT INTO conversations (
                    user_id,
                    user_message,
                    assistant_message
                )
                VALUES (?, ?, ?)
                """,
                (
                    user["id"],
                    req.message,
                    answer,
                ),
            )

    return {"answer": answer}


# ---------------------------------------------------------
# Saved conversations
# ---------------------------------------------------------

@app.get("/api/conversations")
async def list_conversations(
    request: Request,
    limit: int = 50,
):
    user = authenticated_user(request)

    if not user:
        raise HTTPException(
            status_code=401,
            detail="Sign in to view saved conversations",
        )

    limit = max(1, min(limit, 100))

    with db_connect() as conn:
        rows = conn.execute(
            """
            SELECT id, created_at, user_message, assistant_message
            FROM conversations
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (user["id"], limit),
        ).fetchall()

    return {
        "items": [
            {
                "id": row[0],
                "created_at": row[1],
                "user_message": row[2],
                "assistant_message": row[3],
            }
            for row in rows
        ]
    }


@app.delete("/api/conversations")
async def clear_conversations(request: Request):
    user = authenticated_user(request)

    if not user:
        raise HTTPException(
            status_code=401,
            detail="Sign in to delete saved conversations",
        )

    with db_connect() as conn:
        conn.execute(
            "DELETE FROM conversations WHERE user_id = ?",
            (user["id"],),
        )

    return {"status": "deleted"}


# ---------------------------------------------------------
# OpenAI-compatible chat endpoint
# ---------------------------------------------------------

@app.post("/v1/chat/completions")
async def openai_compatible(request: Request):
    enforce_rate_limit(request)
    authenticated_user(request)

    try:
        body = await request.json()

    except (ValueError, UnicodeDecodeError):
        raise HTTPException(
            status_code=400,
            detail="Request body must be valid JSON",
        )

    if not isinstance(body, dict):
        raise HTTPException(
            status_code=400,
            detail="Request body must be a JSON object",
        )

    messages = body.get("messages", [])

    if not isinstance(messages, list) or not messages:
        raise HTTPException(
            status_code=400,
            detail="A non-empty messages array is required",
        )

    if len(messages) > settings.max_history_messages + 2:
        raise HTTPException(
            status_code=400,
            detail="Too many messages in request",
        )

    safe_messages = []
    total_chars = 0

    for item in messages:
        if (
            not isinstance(item, dict)
            or item.get("role") not in (
                "system",
                "user",
                "assistant",
            )
            or not isinstance(item.get("content"), str)
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Each message must have a supported role "
                    "and string content"
                ),
            )

        content = item["content"]
        total_chars += len(content)

        if (
            len(content) > settings.max_message_chars
            or total_chars > settings.max_request_bytes
        ):
            raise HTTPException(
                status_code=413,
                detail="Message content is too large",
            )

        safe_messages.append({
            "role": item["role"],
            "content": content,
        })

    answer = await model_chat(safe_messages)

    return {
        "id": "humanity-ai-local",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": answer,
                },
                "finish_reason": "stop",
            }
        ],
    }
