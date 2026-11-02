from fastapi.testclient import TestClient
import app.main as main

client = TestClient(main.app)

def test_register_login_me_and_delete(monkeypatch, tmp_path):
    monkeypatch.setattr(main.settings, "database_path", str(tmp_path / "accounts.db"))
    monkeypatch.setattr(main.settings, "auth_secret", "test-secret-that-is-long-enough-for-tests-123")
    monkeypatch.setattr(main.settings, "auth_required", True)
    payload = {"email": "person@example.com", "password": "a-very-long-test-password"}
    registered = client.post("/api/auth/register", json=payload)
    assert registered.status_code == 200
    token = registered.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/auth/me", headers=headers).json()["email"] == payload["email"]
    assert client.post("/api/auth/login", json=payload).status_code == 200
    assert client.delete("/api/auth/me", headers=headers).status_code == 200
    assert client.get("/api/auth/me", headers=headers).status_code == 401
    monkeypatch.setattr(main.settings, "auth_required", False)

def test_duplicate_email_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(main.settings, "database_path", str(tmp_path / "duplicate.db"))
    monkeypatch.setattr(main.settings, "auth_secret", "test-secret-that-is-long-enough-for-tests-123")
    payload = {"email": "person@example.com", "password": "a-very-long-test-password"}
    assert client.post("/api/auth/register", json=payload).status_code == 200
    assert client.post("/api/auth/register", json=payload).status_code == 409


def test_chat_requires_account_when_enabled(monkeypatch, tmp_path):
    monkeypatch.setattr(main.settings, "database_path", str(tmp_path / "required.db"))
    monkeypatch.setattr(main.settings, "auth_secret", "test-secret-that-is-long-enough-for-tests-123")
    monkeypatch.setattr(main.settings, "auth_required", True)
    response = client.post("/api/chat", json={"message": "Hello"})
    assert response.status_code == 401
    monkeypatch.setattr(main.settings, "auth_required", False)

def test_pwa_assets_are_served():
    assert client.get("/static/manifest.webmanifest").status_code == 200
    assert client.get("/static/sw.js").status_code == 200


def test_saved_conversations_are_private_and_deletable(monkeypatch, tmp_path):
    monkeypatch.setattr(main.settings, "database_path", str(tmp_path / "private.db"))
    monkeypatch.setattr(main.settings, "auth_secret", "test-secret-that-is-long-enough-for-tests-123")
    monkeypatch.setattr(main.settings, "auth_required", True)
    monkeypatch.setattr(main.settings, "store_conversations", True)
    async def fake_model_chat(messages):
        return "A private answer"
    monkeypatch.setattr(main, "model_chat", fake_model_chat)
    one = client.post("/api/auth/register", json={"email": "one@example.com", "password": "long-password-123"}).json()["token"]
    two = client.post("/api/auth/register", json={"email": "two@example.com", "password": "long-password-456"}).json()["token"]
    h1 = {"Authorization": f"Bearer {one}"}
    h2 = {"Authorization": f"Bearer {two}"}
    assert client.post("/api/chat", headers=h1, json={"message": "secret question"}).status_code == 200
    items = client.get("/api/conversations", headers=h1).json()["items"]
    assert len(items) == 1 and items[0]["user_message"] == "secret question"
    assert client.get("/api/conversations", headers=h2).json()["items"] == []
    assert client.delete("/api/conversations", headers=h1).json()["status"] == "deleted"
    assert client.get("/api/conversations", headers=h1).json()["items"] == []
    monkeypatch.setattr(main.settings, "auth_required", False)
    monkeypatch.setattr(main.settings, "store_conversations", False)


def test_account_deletion_removes_saved_conversations(monkeypatch, tmp_path):
    monkeypatch.setattr(main.settings, "database_path", str(tmp_path / "delete-data.db"))
    monkeypatch.setattr(main.settings, "auth_secret", "test-secret-that-is-long-enough-for-tests-123")
    monkeypatch.setattr(main.settings, "auth_required", True)
    monkeypatch.setattr(main.settings, "store_conversations", True)
    async def fake_model_chat(messages): return "Saved answer"
    monkeypatch.setattr(main, "model_chat", fake_model_chat)
    token = client.post("/api/auth/register", json={"email": "erase@example.com", "password": "long-password-789"}).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    client.post("/api/chat", headers=headers, json={"message": "erase me"})
    assert len(client.get("/api/conversations", headers=headers).json()["items"]) == 1
    assert client.delete("/api/auth/me", headers=headers).status_code == 200
    assert client.get("/api/auth/me", headers=headers).status_code == 401
    with main.db_connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 0
    monkeypatch.setattr(main.settings, "auth_required", False)
    monkeypatch.setattr(main.settings, "store_conversations", False)
