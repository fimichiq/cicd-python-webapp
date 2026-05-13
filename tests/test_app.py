import pytest

from app.main import create_app


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def test_index_returns_message(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.get_json() == {"message": "cicd-python-webapp"}


def test_health_returns_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_version_defaults_to_dev(client, monkeypatch):
    monkeypatch.delenv("APP_VERSION", raising=False)
    fresh_app = create_app()
    response = fresh_app.test_client().get("/version")
    assert response.status_code == 200
    assert response.get_json() == {"version": "dev"}


def test_version_reads_env(monkeypatch):
    monkeypatch.setenv("APP_VERSION", "1.2.3")
    fresh_app = create_app()
    response = fresh_app.test_client().get("/version")
    assert response.status_code == 200
    assert response.get_json() == {"version": "1.2.3"}
