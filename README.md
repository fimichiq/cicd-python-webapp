# cicd-python-webapp

A minimal Python web application used as the deployment target for an end-to-end
CI/CD pipeline running on GitHub Actions and microk8s.

This repository is built incrementally to demonstrate a natural progression of a
deployment process — from a single Flask endpoint, through containerization and
CI, to a multi-environment Kubernetes deployment with rollback and managed secrets.
Each commit represents a self-contained step.

## Endpoints

| Path       | Purpose                                                        |
| ---------- | -------------------------------------------------------------- |
| `GET /`        | Hello / app identity (`{"message": "cicd-python-webapp"}`) |
| `GET /health`  | Liveness/readiness probe target (`{"status": "ok"}`)       |
| `GET /version` | Reports `APP_VERSION` env var (defaults to `dev`)          |

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

# run the app (Flask dev server)
APP_VERSION=local python -m app.main

# in another shell
curl -s localhost:8000/health
curl -s localhost:8000/version
```

## Run tests

```bash
pytest
```
