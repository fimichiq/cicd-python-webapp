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


def test_version_defaults_to_dev(monkeypatch):
    monkeypatch.delenv("APP_VERSION", raising=False)
    monkeypatch.delenv("SECRET_KEY", raising=False)
    response = create_app().test_client().get("/version")
    assert response.status_code == 200
    body = response.get_json()
    assert body["version"] == "dev"
    assert body["secret_source"] == "generated"
    assert len(body["secret_fingerprint"]) == 8


def test_version_reads_env(monkeypatch):
    monkeypatch.setenv("APP_VERSION", "1.2.3")
    response = create_app().test_client().get("/version")
    assert response.status_code == 200
    assert response.get_json()["version"] == "1.2.3"


def test_version_secret_fingerprint_reflects_env(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "first-key-value")
    fp1 = create_app().test_client().get("/version").get_json()["secret_fingerprint"]
    source1 = create_app().test_client().get("/version").get_json()["secret_source"]
    assert source1 == "env"

    monkeypatch.setenv("SECRET_KEY", "second-key-value")
    fp2 = create_app().test_client().get("/version").get_json()["secret_fingerprint"]

    # Same env value → same fingerprint across recreates; different value → different fingerprint.
    assert fp1 != fp2


def test_admin_503_when_token_not_configured(monkeypatch):
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    response = create_app().test_client().get("/admin")
    assert response.status_code == 503
    assert response.get_json() == {"error": "admin endpoint not configured"}


def test_admin_401_without_authorization_header(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "s3cret")
    response = create_app().test_client().get("/admin")
    assert response.status_code == 401
    assert "Authorization" in response.get_json()["error"]


def test_admin_401_with_non_bearer_scheme(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "s3cret")
    response = (
        create_app()
        .test_client()
        .get(
            "/admin",
            headers={"Authorization": "Basic dXNlcjpwYXNz"},
        )
    )
    assert response.status_code == 401


def test_admin_401_with_wrong_token(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "s3cret")
    response = (
        create_app()
        .test_client()
        .get(
            "/admin",
            headers={"Authorization": "Bearer wrong"},
        )
    )
    assert response.status_code == 401
    assert response.get_json() == {"error": "invalid token"}


def test_admin_200_with_correct_token(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "s3cret")
    response = (
        create_app()
        .test_client()
        .get(
            "/admin",
            headers={"Authorization": "Bearer s3cret"},
        )
    )
    assert response.status_code == 200
    assert response.get_json() == {"status": "authenticated", "message": "hello, admin"}
