import pytest
from fastapi.testclient import TestClient
import app.main as main

client = TestClient(main.app)


def test_chat_rejects_blank_message():
    response = client.post('/api/chat', json={'message': '   '})
    assert response.status_code == 422


def test_chat_uses_model_and_returns_answer(monkeypatch):
    async def fake_model_chat(messages):
        assert messages[-1] == {'role': 'user', 'content': 'Hello'}
        return 'Hello back.'
    monkeypatch.setattr(main, 'model_chat', fake_model_chat)
    response = client.post('/api/chat', json={'message': ' Hello '})
    assert response.status_code == 200
    assert response.json() == {'answer': 'Hello back.'}


def test_chat_discards_unsupported_history_roles(monkeypatch):
    async def fake_model_chat(messages):
        assert all(m['role'] in ('system', 'user', 'assistant') for m in messages)
        assert messages[-2:] == [{'role': 'assistant', 'content': 'Earlier'}, {'role': 'user', 'content': 'Now'}]
        return 'OK'
    monkeypatch.setattr(main, 'model_chat', fake_model_chat)
    response = client.post('/api/chat', json={
        'message': 'Now',
        'history': [{'role': 'tool', 'content': 'ignore'}, {'role': 'assistant', 'content': 'Earlier'}],
    })
    assert response.status_code == 200


def test_compat_endpoint_rejects_empty_messages():
    response = client.post('/v1/chat/completions', json={'messages': []})
    assert response.status_code == 400


def test_compat_endpoint_rejects_invalid_message_shape():
    response = client.post('/v1/chat/completions', json={'messages': [{'role': 'user', 'content': 123}]})
    assert response.status_code == 400


def test_compat_endpoint_rejects_oversized_declared_body():
    response = client.post('/v1/chat/completions', content='{}', headers={'content-length': str(main.settings.max_request_bytes + 1)})
    assert response.status_code == 413


def test_compat_endpoint_calls_model(monkeypatch):
    async def fake_model_chat(messages):
        assert messages == [{'role': 'user', 'content': 'Hi'}]
        return 'Welcome.'
    monkeypatch.setattr(main, 'model_chat', fake_model_chat)
    response = client.post('/v1/chat/completions', json={'messages': [{'role': 'user', 'content': 'Hi'}]})
    assert response.status_code == 200
    assert response.json()['choices'][0]['message']['content'] == 'Welcome.'


def test_compat_endpoint_rejects_oversized_actual_body_without_length(monkeypatch):
    response = client.post('/v1/chat/completions', content=' ' * (main.settings.max_request_bytes + 1))
    assert response.status_code == 413


def test_api_key_protects_chat_routes(monkeypatch):
    monkeypatch.setattr(main.settings, 'app_api_key', 'test-secret')
    denied = client.post('/api/chat', json={'message': 'Hello'})
    allowed = client.post('/api/chat', json={'message': 'Hello'}, headers={'Authorization': 'Bearer test-secret'})
    assert denied.status_code == 401
    # Model is not configured in this test, so authentication is proven by reaching the model call.
    assert allowed.status_code == 502
    monkeypatch.setattr(main.settings, 'app_api_key', '')


def test_model_rejects_non_text_content(monkeypatch):
    import httpx
    class FakeResponse:
        def raise_for_status(self): pass
        def json(self): return {'choices': [{'message': {'content': None}}]}
    class FakeClient:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def post(self, *args, **kwargs): return FakeResponse()
    monkeypatch.setattr(main.httpx, 'AsyncClient', FakeClient)
    with pytest.raises(Exception) as exc:
        import asyncio
        asyncio.run(main.model_chat([{'role': 'user', 'content': 'Hi'}]))
    assert getattr(exc.value, 'status_code', None) == 502


def test_model_chat_accepts_valid_openai_compatible_response(monkeypatch):
    class FakeResponse:
        def raise_for_status(self): pass
        def json(self): return {"choices": [{"message": {"content": "Model is online."}}]}
    class FakeClient:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def post(self, url, **kwargs):
            assert url.endswith('/chat/completions')
            assert kwargs['json']['model'] == main.settings.model_name
            return FakeResponse()
    monkeypatch.setattr(main.httpx, 'AsyncClient', FakeClient)
    import asyncio
    assert asyncio.run(main.model_chat([{'role': 'user', 'content': 'Ping'}])) == 'Model is online.'
