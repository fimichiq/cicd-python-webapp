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

## Run in Docker

```bash
docker build -t cicd-python-webapp:dev .
docker run --rm -p 8000:8000 -e APP_VERSION=docker cicd-python-webapp:dev

# in another shell
curl -s localhost:8000/health
curl -s localhost:8000/version
```

The image is multi-stage (builder + slim runtime), runs **gunicorn** with two
workers, and executes as a non-root user (UID `10001`). A built-in `HEALTHCHECK`
hits `/health` every 30 seconds.

## Deploy manually to microk8s

One-time cluster setup:

```bash
microk8s enable ingress dns
echo "127.0.0.1 dev.app.local staging.app.local prod.app.local" | sudo tee -a /etc/hosts
```

Set the GHCR owner in the overlay (replace `OWNER` with your GitHub username):

```bash
sed -i 's|ghcr.io/OWNER/|ghcr.io/<your-gh-user>/|g' k8s/overlays/*/kustomization.yaml
```

Deploy the **dev** environment:

```bash
kubectl kustomize k8s/overlays/dev | kubectl apply -f -
kubectl rollout status deployment/app -n app-dev --timeout=120s

curl -s http://dev.app.local/health
curl -s http://dev.app.local/version
```

For `staging` and `prod`, swap the overlay path. Each lives in its own namespace
(`app-dev`, `app-staging`, `app-prod`) and has a distinct ingress host.
