from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_health():
    response = client.get('/health')
    assert response.status_code == 200
    assert response.json()['status'] == 'ok'


def test_home():
    response = client.get('/')
    assert response.status_code == 200
    assert 'Humanity AI' in response.text


def test_ready_returns_service_unavailable_when_model_is_down(monkeypatch):
    import app.main as main
    class FailedClient:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def get(self, *args, **kwargs):
            import httpx
            request = httpx.Request('GET', 'http://model.invalid/models')
            raise httpx.ConnectError('offline', request=request)
    monkeypatch.setattr(main.httpx, 'AsyncClient', FailedClient)
    response = client.get('/ready')
    assert response.status_code == 503
