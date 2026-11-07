# Bootstrap: rebuilding the kind cluster and recovering Flux

How to get from nothing to the running `sample-api` kind cluster, and how to recover when part of Flux is gone. Everything
the cluster runs comes from Git; only the commands here happen by hand. Flux reads `main`, so what you get is what `main` holds.

Run everything from WSL. The design and its evidence are in
[`docs/design/module-8-gitops-flux.md`](design/module-8-gitops-flux.md).

## Before you start

You need Docker Desktop running, with its WSL integration on, and these in WSL: `kind`, `kubectl`, `flux`, `helm`, and `gh`
logged in (for [replacing the write key](#replace-the-write-key)). This guide last ran with kind v0.33.0, kubectl v1.36.0,
flux v2.10.0 and gh 2.86.0. Keep the `flux` CLI at the cluster's Flux version, v2.10.0.

Helm is the only one this repo pins by hand. To install it once, into `~/.local/bin`:

```bash
cd "$(mktemp -d)" && curl -fsSLO https://get.helm.sh/helm-v4.3.1-linux-amd64.tar.gz \
  && echo "0403a88ae20913d11b10a2467b50087721fea7581736da6b9d3992f5357fe54b  helm-v4.3.1-linux-amd64.tar.gz" | sha256sum -c - \
  && tar -xzf helm-v4.3.1-linux-amd64.tar.gz linux-amd64/helm && install -D -m 0755 linux-amd64/helm ~/.local/bin/helm \
  && ~/.local/bin/helm version --short
```

If the checksum line doesn't print `OK`, the chain stops and nothing is installed. On a new machine, open a new terminal
afterwards, so `~/.local/bin` is on your `PATH`.

Flux also needs its write key at `~/.ssh/flux-image-updates`, registered on the repo as the deploy key `flux-image-updates`.
Guard it like an admin credential: GitHub lets it bypass the `mod*` ruleset (see [`docs/iam.md`](iam.md)). If it's lost or may
have leaked, [replace it](#replace-the-write-key).

Run everything from an up-to-date `main`, because step 3 applies `flux-instance.yaml` from your checkout. If the repo isn't on
this machine yet, clone it to the path every command below uses:

```bash
git clone https://github.com/kerinschristopher/Curriculum.git /mnt/c/Work/GitHub/Curriculum
```

If it's already there, update it instead:

```bash
cd /mnt/c/Work/GitHub/Curriculum && git switch main && git pull --ff-only
```

## Rebuild the cluster

**1. Delete the old cluster, if there is one.** This removes everything in it. Skip this step on a first build.

```bash
kind delete cluster --name sample-api
```

**2. Create the cluster.** `kind` also switches `kubectl` to it.

```bash
cd /mnt/c/Work/GitHub/Curriculum
kind create cluster --config Acme/platform/kind/sample-api-cluster.yaml
kubectl config current-context
```

The last line must print `kind-sample-api`.

**3. Install the Operator, then Flux.**

```bash
cd /mnt/c/Work/GitHub/Curriculum && helm install flux-operator oci://ghcr.io/controlplaneio-fluxcd/charts/flux-operator \
  --version 0.62.1 --namespace flux-system --create-namespace --values Acme/infra/bootstrap/flux-operator-values.yaml --wait \
  && kubectl apply -f Acme/infra/bootstrap/flux-instance.yaml \
  && kubectl -n flux-system wait fluxinstance/flux --for=condition=Ready --timeout=10m
```

From here Flux does the rest. It reads `Acme/infra/bootstrap/` from `main`, then applies ingress-nginx and all three
environments. It also takes over the Operator's Helm release, so a new Operator pod replacing the first one is expected.

**4. Create the Secret for Flux's write key.** It's the one thing Git can't supply. `flux create` also stores GitHub's host key.

```bash
flux create secret git flux-image-updates --namespace=flux-system \
  --url=ssh://git@github.com/kerinschristopher/Curriculum.git --private-key-file="$HOME/.ssh/flux-image-updates"
```

**5. Wait for everything to be Ready.** The block stops at the first wait that fails. An `apps-*` Kustomization is Ready
once its objects are applied, not once its pods run, so the sample-api lines wait for the pods: on a fresh cluster the
image pull can take a few minutes. Each timeout is a limit, not a delay. `infrastructure` also installs the monitoring
chart, kube-prometheus-stack (Prometheus, Alertmanager and Grafana), and is Ready once Helm has finished. Prometheus and
Alertmanager aren't objects the chart creates: the chart's operator creates them while the install runs, so Helm
finishing doesn't promise they're ready, and the last lines wait for each to exist and then to be ready. The first install
took about 2 minutes on a laptop; the 10-minute limit leaves room for a slow image pull.

```bash
(
set -eu
for name in infrastructure apps-dev apps-stage apps-prod; do
  kubectl -n flux-system wait "kustomization/$name" --for=create --timeout=5m \
    && kubectl -n flux-system wait "kustomization/$name" --for=condition=Ready --timeout=10m \
    || exit 1
done
kubectl -n flux-system wait helmrelease/flux-operator --for=create --timeout=5m \
  && kubectl -n flux-system wait helmrelease/flux-operator --for=condition=Ready --timeout=5m \
  || exit 1
for env in dev stage prod; do
  kubectl -n "sample-api-$env" rollout status deployment/sample-api --timeout=5m
done
for sts in prometheus-kube-prometheus-stack-prometheus alertmanager-kube-prometheus-stack-alertmanager; do
  kubectl -n monitoring wait "statefulset/$sts" --for=create --timeout=5m \
    && kubectl -n monitoring rollout status "statefulset/$sts" --timeout=5m \
    || exit 1
done
)
```

**6. Check it.**

```bash
flux get kustomizations
helm history -n flux-system flux-operator
helm history -n monitoring kube-prometheus-stack
kubectl get pods -n monitoring
kubectl get deployments -n sample-api-dev
kubectl get deployments -n sample-api-stage
kubectl get deployments -n sample-api-prod
curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: sample-api.dev.local' localhost/health
flux reconcile source git flux-image-updates
kubectl -n flux-system wait gitrepository/flux-image-updates --for=condition=Ready --timeout=2m
```

You should see:

- All five Kustomizations `True`, at the same `main` commit.
- Helm revision 2, chart `flux-operator-0.62.1+1135514a01cc`. That chart name means Flux made the revision; revision 1,
  `flux-operator-0.62.1`, is your `helm install`.
- Helm revision 1 for `kube-prometheus-stack`, chart `kube-prometheus-stack-92.21.2+4ae2ebc21830`. Flux installed it from
  the chart's digest; there's no hand-made revision.
- Six pods `Running` in `monitoring`: Prometheus, Alertmanager, Grafana, the Prometheus Operator, kube-state-metrics and
  node-exporter.
- `sample-api` at 1/1 in dev, 2/2 in stage and 3/3 in prod.
- `200` from the health check.
- `condition met` for `flux-image-updates`, which proves the Secret works and GitHub accepts the key.

## Open Grafana

Grafana has no ingress; reach it through a port-forward. The chart generates the admin password when it installs and keeps
it in a Secret, so it's different on every cluster.

```bash
kubectl -n monitoring get secret kube-prometheus-stack-grafana -o jsonpath='{.data.admin-password}' | base64 -d; echo
kubectl -n monitoring port-forward svc/kube-prometheus-stack-grafana 3000:80
```

Open http://localhost:3000 and log in as `admin` with the printed password.

## Recover Flux

First work out which case you're in.

| What happened | What's left | Recovery |
|---|---|---|
| `flux-system` namespace deleted, and it's stuck `Terminating` | Flux's CRDs and your workloads. The Operator, its Helm record and the `ResourceSet` are gone | All five steps below |
| `FluxInstance` deleted | The namespace, the Operator, Helm's record, the `ResourceSet` and your workloads. Flux's controllers and CRDs are gone | Steps 3 and 5 |

Your workloads keep running in both cases.

**1. Clear the stuck finalizers.** A finalizer is a note telling Kubernetes to wait for a controller's cleanup before deleting
an object. Deleting the namespace also deleted those controllers, so nothing is left to do the cleanup, and the namespace
waits forever. This removes the notes:

```bash
for kind in $(kubectl api-resources --namespaced --verbs=list -o name | grep -E 'fluxcd\.(io|controlplane\.io)$'); do
  for object in $(kubectl -n flux-system get "$kind" -o name); do
    kubectl -n flux-system patch "$object" --type=merge -p '{"metadata":{"finalizers":null}}'
  done
done
kubectl wait --for=delete namespace/flux-system --timeout=2m
```

The last line returns once the namespace has gone. No cleanup runs, so the workloads and CRDs stay.

**2. Reinstall the Operator.** This isn't the same command as a rebuild. A plain `helm install` fails with server-side apply
conflicts, because Flux owns fields on the objects the namespace delete left behind. `--force-conflicts` takes them back, and
`--take-ownership` doesn't help.

```bash
cd /mnt/c/Work/GitHub/Curriculum && helm upgrade --install flux-operator oci://ghcr.io/controlplaneio-fluxcd/charts/flux-operator \
  --version 0.62.1 --namespace flux-system --create-namespace --values Acme/infra/bootstrap/flux-operator-values.yaml \
  --force-conflicts --wait
```

**3. Put Flux back.**

```bash
cd /mnt/c/Work/GitHub/Curriculum && kubectl apply -f Acme/infra/bootstrap/flux-instance.yaml \
  && kubectl -n flux-system wait fluxinstance/flux --for=condition=Ready --timeout=10m
```

**4. Re-create the Secret.** Deleting the namespace deleted it too.

```bash
flux create secret git flux-image-updates --namespace=flux-system \
  --url=ssh://git@github.com/kerinschristopher/Curriculum.git --private-key-file="$HOME/.ssh/flux-image-updates"
```

**5. Nudge the `ResourceSet`, then check the Operator's release.** Do this every time, so you needn't work out which case
applies. A `ResourceSet` runs hourly, and it doesn't watch the objects it generates. If it's still showing Ready, nothing
recreates the `HelmRelease` until the next hourly run. If it had nothing to do, this costs one extra run.

```bash
kubectl -n flux-system wait resourceset/flux-operator --for=create --timeout=5m \
  && kubectl -n flux-system annotate resourceset flux-operator reconcile.fluxcd.io/requestedAt="$(date +%s)" --overwrite \
  && kubectl -n flux-system wait helmrelease/flux-operator --for=create --timeout=5m \
  && kubectl -n flux-system wait helmrelease/flux-operator --for=condition=Ready --timeout=5m
```

Then run the checks in [step 6](#rebuild-the-cluster). After a namespace delete the Helm revision is 2 again, because the delete
took Helm's history with it. After a `FluxInstance` delete it's higher than 2. Flux removes the `requestedAt` annotation at its
next reconcile, up to 10 minutes later. That's normal, and it means the annotate worked.

## Replace the write key

Do this if the key is lost or may have leaked, without waiting to be sure. The old key works until its deploy key is deleted,
so that happens first. Flux can't push until the block finishes, which does no harm. The first command only reads GitHub's key
list, so if `gh` isn't logged in or can't reach the repo, the block stops before anything changes.

```bash
(
set -eu
cd /mnt/c/Work/GitHub/Curriculum
old=$(gh repo deploy-key list --json id,title --jq '.[] | select(.title == "flux-image-updates") | .id')
rm -f ~/.ssh/flux-image-updates ~/.ssh/flux-image-updates.pub
ssh-keygen -q -t ed25519 -N '' -C flux-image-updates -f ~/.ssh/flux-image-updates
if [ -n "$old" ]; then gh repo deploy-key delete "$old"; else echo "No old deploy key on GitHub; adding the new one"; fi
gh repo deploy-key add ~/.ssh/flux-image-updates.pub --allow-write --title flux-image-updates
[ "$(kubectl config current-context)" = kind-sample-api ] || { echo "kubectl isn't on kind-sample-api; stopping before flux create"; exit 1; }
flux create secret git flux-image-updates --namespace=flux-system \
  --url=ssh://git@github.com/kerinschristopher/Curriculum.git --private-key-file="$HOME/.ssh/flux-image-updates"
flux reconcile source git flux-image-updates
kubectl -n flux-system wait gitrepository/flux-image-updates --for=condition=Ready --timeout=2m
)
```

`condition met` proves GitHub accepts the new key. If `kubectl` has no context (as after `kind delete cluster`) or is on another
cluster, the block stops at the context check, just before `flux create`; if the cluster is only stopped, the check passes and
the block stops at `flux create`. Either way the key is already replaced on GitHub, and rebuild step 4 creates the Secret later.
If the key may have leaked, also check what it could have done: see
[After a leak of the write key](iam.md#after-a-leak-of-the-write-key).

`gh` ties a key it adds to your login token, so revoking that token removes the deploy key and Flux can't push. If that happens,
run this again.

## Removing Flux safely

Delete the `FluxInstance`, never the namespace:

```bash
kubectl -n flux-system delete fluxinstance flux --timeout=3m
```

The Operator removes Flux's controllers, objects and CRDs. It keeps the namespace, itself, the `ResourceSet` and your workloads.
Afterwards the `ResourceSet` can still show Ready, up to an hour. That only means its last run worked, so check the
`HelmRelease` itself. To bring Flux back, follow steps 3 and 5 of [Recover Flux](#recover-flux).

## Things that look wrong but aren't

- **A hand `helm upgrade` fails or doesn't last.** Once Flux has taken over, a plain `helm upgrade` fails on conflicts and
  leaves a `failed` revision. With `--force-conflicts` it succeeds, but Flux undoes it at its next reconcile, within 30 minutes.
  Change the Operator by PR instead: `version` in `flux-operator.yaml`, `tag@digest` in `flux-operator-values.yaml`, CI's
  Operator version and checksums, and the two Helm commands in this file.
- **`helm history` mixes time zones.** Revisions from the CLI print in local time, and Flux's in UTC.
- **`apps-*` briefly not Ready after a merge.** Each one waits for `infrastructure` to reach the same commit, then retries.
- **"skipped" in `flux-system`'s events.** If `flux-instance.yaml` or `flux-operator.yaml` leaves Git, Flux deletes nothing
  and reports it as skipped. Those objects are protected from pruning, and they stay unmanaged until the file returns.
- **`flux get` shows an old commit.** Check that the cluster has been running: `docker inspect sample-api-control-plane --format '{{.State.StartedAt}}'`.
- **A PR from `flux/image-updates` shows conflicts.** Flux creates that branch from `main` only when it doesn't exist. If it
  exists, Flux commits on top of it, and anything already there stays, hand commits included. Delete the branch, which also
  closes any open PR from it. If dev isn't on the latest release, Flux recreates the branch from `main` at its next run, up to
  10 minutes later, so open a new PR then. To avoid this, delete the branch after merging each PR, and never push to it: change
  an image by merging its PR, or edit the overlay in your own branch.
