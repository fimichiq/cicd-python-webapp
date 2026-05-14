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

On hosts running **firewalld** (Fedora, RHEL, CentOS) the pod and service CIDRs
**and the Calico CNI interfaces** must be in the `trusted` zone, otherwise
inter-pod traffic is silently dropped and ingress times out with
`504 Gateway Time-out`:

```bash
sudo firewall-cmd --permanent --zone=trusted --add-source=10.1.0.0/16
sudo firewall-cmd --permanent --zone=trusted --add-source=10.152.183.0/24
sudo firewall-cmd --permanent --zone=trusted --add-interface=vxlan.calico
sudo firewall-cmd --permanent --zone=trusted --add-interface=cali+
sudo firewall-cmd --reload
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

## Continuous deployment to `dev`

Once CI publishes an image to GHCR, the `CD (dev)` workflow
(`.github/workflows/cd-dev.yml`) deploys that exact commit to the local
microk8s cluster.

Flow:

1. `CI` finishes successfully on `main` and publishes
   `ghcr.io/<owner>/cicd-python-webapp:sha-<short>`.
2. `CD (dev)` is triggered via `workflow_run`, pinned to that commit's image
   tag in `k8s/overlays/dev/kustomization.yaml`.
3. `kubectl apply -k k8s/overlays/dev` + `kubectl rollout status` (120 s).
4. Smoke test: `kubectl exec` into the new pod and `GET /health`.
5. On any failure: `kubectl rollout undo` and re-wait — the previous
   ReplicaSet takes over.

The smoke test runs *inside* the pod rather than through the Ingress; the
local cluster has an unrelated CNI/iptables issue that breaks ingress→pod
traffic. The in-pod check is a stronger gate for "is the rollout healthy"
anyway — it decouples application health from ingress correctness.

You can also trigger CD manually:

```bash
gh workflow run "CD (dev)" -f sha=<full-or-empty-for-HEAD>
```

### Deploy a feature branch to `dev`

`dev` is intentionally a "does it run" environment, so you can sideload any
branch's HEAD into it without going through `main`. The flow:

1. Push your feature branch (`git push origin feature/<x>`). CI builds and
   pushes a `sha-<short>` image for that commit to GHCR — feature branches
   build images too, only `main` gets the floating `:main` tag and only `v*`
   tags get semver tags.
2. Wait for CI on that branch to go green (otherwise the image won't exist).
3. Trigger `CD (dev)` against your branch:

   - **In the GitHub UI** — Actions tab → "CD (dev)" → "Run workflow" →
     change "Use workflow from" to your feature branch → leave `sha` empty →
     "Run workflow". The job uses `github.sha` of the dispatched ref, which
     is your branch's HEAD.
   - **From the CLI** — `gh workflow run "CD (dev)" --ref feature/<x>`.

`app-dev` will now run the feature branch's commit. Push another commit to
the same branch and dispatch again to update — `cd-dev.yml` enforces
`concurrency: cd-dev` so two parallel dev deploys can't fight over the
namespace. To return `app-dev` to `main`, dispatch `CD (dev)` against `main`
or just push to `main` and let CI fire CD automatically.

## Promote to `staging`

The `CD (staging)` workflow (`.github/workflows/cd-staging.yml`) deploys a
release-candidate to the `app-staging` namespace, gated by a manual approval
step.

Flow:

1. Tag a commit on `main` (or any branch) with a release-candidate semver:

   ```bash
   git tag v0.1.0-rc1
   git push origin v0.1.0-rc1
   ```

2. CI runs on the tag push and publishes the image to GHCR with the semver
   tag (the leading `v` is stripped by `docker/metadata-action`), e.g.
   `ghcr.io/<owner>/cicd-python-webapp:0.1.0-rc1`.
3. `CD (staging)` is triggered via `workflow_run` once CI is green. It pauses
   in the `staging` Environment waiting for a Required reviewer to approve.
4. After approval: pin the image tag in `k8s/overlays/staging`, `kubectl
   apply -k`, wait for rollout, run integration tests inside the pod
   (`/`, `/health`, `/version` — the last one must report `version=staging`,
   catching a wrong-overlay misconfiguration that a bare `/health` would
   miss).
5. On any failure: `kubectl rollout undo` and re-wait.

You can also promote manually — useful for rehearsing staging against a
feature commit without cutting an RC tag:

```bash
gh workflow run "CD (staging)" -f image_tag=sha-<short>
# or:
gh workflow run "CD (staging)" -f image_tag=0.1.0-rc1
```

### One-time setup — the `staging` GitHub Environment

The approval gate lives in a repository Environment, not in the workflow
file. Create it once:

1. GitHub → repo → **Settings → Environments → New environment** → name it
   `staging`.
2. Under **Deployment protection rules**, enable **Required reviewers** and
   add yourself (or whoever should approve staging deploys).
3. Save. The next `CD (staging)` run will pause at the `Deploy to
   app-staging` job until a reviewer clicks **Review deployments → Approve**
   from the run page.

Without this Environment, the workflow runs without a gate — the
`environment: staging` field in the YAML is what *binds* the workflow to the
Environment's protection rules, so the gate only exists once the Environment
itself is configured in GitHub.

## Promote to `prod`

The `CD (prod)` workflow (`.github/workflows/cd-prod.yml`) deploys a final
release to the `app-prod` namespace, gated by a manual approval step (same
mechanism as staging, but bound to a separate `production` Environment).

Flow:

1. Tag a commit with a final semver (no `-rc` suffix):

   ```bash
   git tag v0.1.0
   git push origin v0.1.0
   ```

2. CI runs on the tag push and publishes the image to GHCR with the semver
   tag (the leading `v` is stripped by `docker/metadata-action`), e.g.
   `ghcr.io/<owner>/cicd-python-webapp:0.1.0`. The `workflow_run` trigger on
   `cd-prod.yml` uses the pattern `['v*', '!v*-rc*']` so RC tags don't fire
   prod — they keep flowing through staging.
3. `CD (prod)` is triggered via `workflow_run` once CI is green. It pauses
   in the `production` Environment waiting for a Required reviewer to
   approve.
4. After approval: pin the image tag in `k8s/overlays/prod`, `kubectl
   apply -k`, wait for rollout, run integration tests inside the pod
   (`/`, `/health`, `/version` — the last one must report
   `version=prod`).
5. On any failure: `kubectl rollout undo` and re-wait.

You can also promote manually — useful for hotfix rehearsals or for pushing
a `sha-<short>` image without cutting a release tag:

```bash
gh workflow run "CD (prod)" -f image_tag=0.1.0
# or:
gh workflow run "CD (prod)" -f image_tag=sha-<short>
```

### One-time setup — the `production` GitHub Environment

Same shape as the staging Environment, just a different name:

1. GitHub → repo → **Settings → Environments → New environment** → name it
   `production`.
2. Under **Deployment protection rules**, enable **Required reviewers** and
   add yourself (or whoever should approve prod deploys — in a real project
   this would typically be a different / larger group than staging's
   reviewers).
3. Save. The next `CD (prod)` run will pause at the `Deploy to app-prod`
   job until a reviewer clicks **Review deployments → Approve**.

## Register the self-hosted runner

CD runs on a self-hosted runner labelled `microk8s`, which is the only thing
that has direct `kubectl` access to the local cluster.

**Prerequisites on the runner host:**

- `kubectl` on `$PATH`, with kubeconfig pointing at the microk8s cluster
  (`microk8s config > ~/.kube/config` works).
- `kustomize` standalone binary on `$PATH` (the workflow uses
  `kustomize edit`, which is not part of `kubectl kustomize`):

  ```bash
  curl -sLo /tmp/kustomize.tgz \
    https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize%2Fv5.4.3/kustomize_v5.4.3_linux_amd64.tar.gz
  tar -xzf /tmp/kustomize.tgz -C /tmp
  sudo install -m 0755 /tmp/kustomize /usr/local/bin/kustomize
  kustomize version
  ```

**Register the runner** (do this once per host):

1. Go to **GitHub → repo → Settings → Actions → Runners → New self-hosted
   runner** and copy the registration token shown there. The token is
   short-lived; generate it just before you run `./config.sh`.

2. Install the runner under `/opt/actions-runner` and configure it:

   ```bash
   sudo mkdir -p /opt/actions-runner
   sudo chown $USER:$USER /opt/actions-runner
   cd /opt/actions-runner

   curl -sLo runner.tgz \
     https://github.com/actions/runner/releases/download/v2.319.1/actions-runner-linux-x64-2.319.1.tar.gz
   tar xzf runner.tgz && rm runner.tgz

   ./config.sh \
     --url https://github.com/<owner>/cicd-python-webapp \
     --token <REGISTRATION_TOKEN> \
     --name microk8s-local \
     --labels microk8s \
     --work _work \
     --unattended
   ```

   **Why `/opt`, not `$HOME`?** On Fedora/RHEL hosts with SELinux in enforcing
   mode, files under `/home/<user>` are labeled `user_home_t`, which the
   targeted policy forbids systemd from execing — the service unit fails with
   `status=203/EXEC`. `/opt` is labeled `usr_t`, which systemd is allowed to
   exec.

   **If you extracted/moved into `/opt` from elsewhere**, the labels don't
   auto-update — `cp -a` / `mv` preserve the source's `user_home_t` and the
   service still fails. Force a relabel against the system policy:

   ```bash
   sudo restorecon -RFv /opt/actions-runner
   ```

3. Tell the runner where to find `kubectl` and the kubeconfig. The runner's
   systemd unit starts with a clean environment, so jobs won't inherit your
   shell's `$PATH` / `$KUBECONFIG`:

   ```bash
   cat > /opt/actions-runner/.env <<'EOF'
   PATH=/home/<user>/.local/bin:/usr/local/bin:/usr/bin:/bin
   KUBECONFIG=/home/<user>/.kube/config
   EOF
   ```

4. Install + start it as a systemd service (so it survives reboot):

   ```bash
   sudo ./svc.sh install $USER
   sudo ./svc.sh start
   sudo ./svc.sh status
   ```

   The runner now picks up jobs targeted at `runs-on: [self-hosted, microk8s]`.

**Verify** it's online: **GitHub → repo → Settings → Actions → Runners** —
`microk8s-local` should be green / `Idle`.

To **remove** the runner, from `/opt/actions-runner`:

```bash
sudo ./svc.sh stop
sudo ./svc.sh uninstall
./config.sh remove --token <REMOVAL_TOKEN>   # token from the same Settings page
```
