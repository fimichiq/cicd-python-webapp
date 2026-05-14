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

## Rollback runbook

Two layers of rollback are built in:

1. **Automatic in-pipeline rollback** — every `CD (*)` workflow runs
   `kubectl rollout undo` if its own rollout-status or smoke step fails.
   This catches "the new revision crashes on boot or fails health checks
   inside the first few minutes".
2. **Manual break-glass rollback** — `.github/workflows/rollback.yml`,
   triggered from the GitHub Actions UI (or `gh workflow run`). Use this
   when a deploy went green in smoke but later degrades — e.g. a memory
   leak, a wrong feature flag, a config drift the smoke test couldn't
   catch.

The manual workflow shares the `cd-<env>` concurrency group with the
forward-deploy workflow, so a rollback and a deploy can never race against
the same namespace. For `staging` and `prod` it also binds to the same
GitHub Environment as the forward deploy — so a prod rollback waits for the
same Required reviewer that gates `CD (prod)`.

### Three scenarios

**Scenario 1 — undo the last deploy (most common).** Latest revision is
broken and the previous revision was healthy:

```bash
gh workflow run Rollback -f environment=prod -f mode=undo
```

This runs `kubectl rollout undo deployment/app -n app-prod` — Kubernetes
flips back to the previous ReplicaSet, which is still in the cluster
(deployments keep `revisionHistoryLimit=10` ReplicaSets by default).

**Scenario 2 — jump to a specific older revision.** The last *two* deploys
were both bad; you want the one three back:

```bash
# Inspect history first
kubectl rollout history deployment/app -n app-prod
# Output:
# REVISION  CHANGE-CAUSE
# 5         <none>
# 6         <none>
# 7         <none>   ← latest, broken
# Re-target revision 5
gh workflow run Rollback -f environment=prod -f mode=to-revision -f target=5
```

This runs `kubectl rollout undo --to-revision=5`. Caveat: rewinding can hit
the `revisionHistoryLimit` cap — if revision 5 has been trimmed, the
command errors. That's when scenario 3 saves you.

**Scenario 3 — replay a known-good image tag.** You know `v0.0.9` was
healthy, but its ReplicaSet has been garbage-collected from history:

```bash
gh workflow run Rollback -f environment=prod -f mode=to-tag -f target=0.0.9
# or with an immutable sha-<short> tag:
gh workflow run Rollback -f environment=prod -f mode=to-tag -f target=sha-abc1234
```

This re-pins `k8s/overlays/prod/kustomization.yaml` to the requested image
tag and re-applies the overlay. Works for any tag that still exists in
GHCR — including pre-release / sha- / floating tags. Useful as a "force a
specific image" knob independent of Kubernetes' revision history.

### Database migrations — out of scope

This app has no database, so rollback is purely a stateless deployment
swap. In a real system with a relational DB, a rollback of the app version
without a paired rollback of the schema migration is unsafe — that's why
production migration tooling typically requires **backward-compatible
migrations**: add a column, deploy code that reads both old and new shapes,
then drop the old column in a later release. The same `Rollback` workflow
remains useful (it rolls back app code) but it cannot un-apply a
migration; that's a separate story per-DB.

## Manage secrets

Two flavours of secret material live in this repo, on different layers:

| Layer | Tool | Examples here |
|---|---|---|
| **Workflow** (GH Actions needs them) | GitHub Secrets | `GITHUB_TOKEN` for GHCR push, future `SLACK_WEBHOOK_URL` |
| **Application** (running pod needs them) | **Bitnami Sealed Secrets** | `SECRET_KEY`, `ADMIN_TOKEN` |

A `kind: Secret` in plain YAML is **not encrypted** — it's just base64.
Committing one to git leaks the value. Sealed Secrets solve this:
`kubeseal` encrypts the values against a **cluster-bound public key**, and
the in-cluster controller is the only party with the matching private
key. The encrypted file IS safe to commit; the same file is **useless on
any other cluster** because no other cluster has the private key.

### One-time cluster setup

Install the controller into the cluster (any namespace; `kube-system` is
conventional):

```bash
microk8s helm repo add sealed-secrets https://bitnami-labs.github.io/sealed-secrets
microk8s helm repo update
microk8s helm install sealed-secrets sealed-secrets/sealed-secrets \
  --namespace kube-system
kubectl rollout status deployment/sealed-secrets -n kube-system
```

Install the `kubeseal` CLI on your workstation (any release ≥0.27 works):

```bash
KUBESEAL_VERSION=0.27.1
curl -sLo /tmp/ks.tgz \
  https://github.com/bitnami-labs/sealed-secrets/releases/download/v${KUBESEAL_VERSION}/kubeseal-${KUBESEAL_VERSION}-linux-amd64.tar.gz
tar -xzf /tmp/ks.tgz -C /tmp kubeseal
sudo install -m 0755 /tmp/kubeseal /usr/local/bin/kubeseal
kubeseal --version
```

### Generate `k8s/base/sealedsecret.yaml` for your cluster

The committed file is a **placeholder**; you must regenerate it once
against your cluster's keypair before the first deploy will succeed:

```bash
# 1. Build the raw Secret with real values (do NOT commit this intermediate file)
kubectl create secret generic app-secret \
  --from-literal=SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')" \
  --from-literal=ADMIN_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')" \
  --dry-run=client -o yaml > /tmp/app-secret.yaml

# 2. Seal it against the cluster's pubkey, scoped cluster-wide so each
#    overlay can adopt the same source in its own namespace.
kubeseal --scope cluster-wide -o yaml < /tmp/app-secret.yaml > k8s/base/sealedsecret.yaml

# 3. Shred the plain Secret immediately — only the encrypted file should
#    leave this machine.
shred -u /tmp/app-secret.yaml

# 4. Commit the new sealedsecret.yaml.
git add k8s/base/sealedsecret.yaml
```

After applying (`kubectl apply -k k8s/overlays/<env>`), the controller
creates the matching `Secret app-secret` in the target namespace, and the
app deployment picks up `SECRET_KEY` + `ADMIN_TOKEN` through
`envFrom: secretRef`. Verify the pipeline end-to-end:

```bash
kubectl get sealedsecret -A
kubectl get secret app-secret -n app-dev -o jsonpath='{.data}'   # decrypted
curl -s http://dev.app.local/version                              # secret_source=env
```

If `/version` reports `"secret_source": "generated"` after a deploy, the
SealedSecret wasn't decrypted — check `kubectl logs -n kube-system
deployment/sealed-secrets` and re-run `kubeseal`. If the pod is stuck in
`CreateContainerConfigError`, the underlying `Secret` was never produced
(same root cause).

### Rotation

Rotation = regenerate the same `sealedsecret.yaml` against the same cluster
with new plaintext values, then redeploy:

```bash
# Same kubeseal pipeline as above, with fresh tokens.
kubectl create secret generic app-secret \
  --from-literal=SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')" \
  --from-literal=ADMIN_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')" \
  --dry-run=client -o yaml | kubeseal --scope cluster-wide -o yaml \
  > k8s/base/sealedsecret.yaml
git commit -am "rotate app secrets"
git push                            # triggers CI → CD; sealed-secrets controller
                                    # updates the Secret; deployment rolls a new
                                    # ReplicaSet because envFrom hash changes.
```

### Admin endpoint demo

After a successful deploy, hit `/admin` with the rotated `ADMIN_TOKEN`:

```bash
# Inspect the decrypted token (locally; you'd normally read it from your
# password manager or 1Password CLI rather than the cluster):
TOKEN=$(kubectl get secret app-secret -n app-dev \
  -o jsonpath='{.data.ADMIN_TOKEN}' | base64 -d)

# Exec into the pod since ingress→pod is broken on this host
POD=$(kubectl get pod -n app-dev -l app.kubernetes.io/name=cicd-python-webapp \
  -o jsonpath='{.items[0].metadata.name}')
kubectl exec -n app-dev "$POD" -- python -c "
import os, urllib.request
req = urllib.request.Request('http://127.0.0.1:8000/admin',
    headers={'Authorization': f'Bearer {os.environ[\"ADMIN_TOKEN\"]}'})
print(urllib.request.urlopen(req).read().decode())
"
```

Empty/missing token → 401, wrong token → 401 (constant-time compared so
length doesn't leak), correct token → `{"status": "authenticated",
"message": "hello, admin"}`. The token comparison uses `hmac.compare_digest`
to avoid timing side-channels.

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
