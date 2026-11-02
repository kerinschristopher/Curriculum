# Bootstrap: rebuilding the kind cluster and recovering Flux

How to get from nothing to the running `sample-api` kind cluster, and how to recover when part of Flux is gone. Everything
the cluster runs comes from Git; only the commands here happen by hand. Flux reads `main`, so what you get is what `main` holds.

Run everything from WSL. The design and its evidence are in
[`docs/design/module-8-gitops-flux.md`](design/module-8-gitops-flux.md).

## Before you start

You need Docker Desktop running, with its WSL integration on, and these in WSL: `kind`, `kubectl`, `flux` and `helm`.

Helm is the only one this repo pins by hand. To install it once, into `~/.local/bin`:

```bash
cd "$(mktemp -d)" && curl -fsSLO https://get.helm.sh/helm-v4.3.1-linux-amd64.tar.gz \
  && echo "0403a88ae20913d11b10a2467b50087721fea7581736da6b9d3992f5357fe54b  helm-v4.3.1-linux-amd64.tar.gz" | sha256sum -c - \
  && tar -xzf helm-v4.3.1-linux-amd64.tar.gz linux-amd64/helm && install -m 0755 linux-amd64/helm ~/.local/bin/helm \
  && helm version --short
```

If the checksum line doesn't print `OK`, the chain stops and nothing is installed.

Flux also needs its write key at `~/.ssh/flux-image-updates`, registered on the repo as the deploy key `flux-image-updates`.
Guard it like an admin credential: GitHub lets it bypass the `mod*` ruleset (see [`docs/iam.md`](iam.md)). If it's lost, make a
new key and replace the deploy key.

Run everything from an up-to-date `main`, because step 3 applies `flux-instance.yaml` from your checkout.

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

**5. Wait for everything to be Ready.**

```bash
for name in infrastructure apps-dev apps-stage apps-prod; do
  kubectl -n flux-system wait "kustomization/$name" --for=create --timeout=5m \
    && kubectl -n flux-system wait "kustomization/$name" --for=condition=Ready --timeout=10m
done
kubectl -n flux-system wait helmrelease/flux-operator --for=create --timeout=5m \
  && kubectl -n flux-system wait helmrelease/flux-operator --for=condition=Ready --timeout=5m
```

**6. Check it.**

```bash
flux get kustomizations
helm history -n flux-system flux-operator
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
- `sample-api` at 1/1 in dev, 2/2 in stage and 3/3 in prod.
- `200` from the health check.
- `condition met` for `flux-image-updates`, which proves the Secret works and GitHub accepts the key.

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
for kind in helmreleases.helm.toolkit.fluxcd.io ocirepositories.source.toolkit.fluxcd.io \
            imagerepositories.image.toolkit.fluxcd.io imagepolicies.image.toolkit.fluxcd.io \
            imageupdateautomations.image.toolkit.fluxcd.io \
            gitrepositories.source.toolkit.fluxcd.io kustomizations.kustomize.toolkit.fluxcd.io \
            fluxinstances.fluxcd.controlplane.io resourcesets.fluxcd.controlplane.io; do
  for object in $(kubectl -n flux-system get "$kind" -o name); do
    kubectl -n flux-system patch "$object" --type=merge -p '{"metadata":{"finalizers":null}}'
  done
done
kubectl get namespace flux-system
```

The last line should print `NotFound`. No cleanup runs, so the workloads and CRDs stay.

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

Then run the checks in [step 6](#rebuild-the-cluster). The Helm revision number will be higher than 2. Flux removes the
`requestedAt` annotation at its next reconcile, up to 10 minutes later. That's normal, and it means the annotate worked.

## Removing Flux safely

Delete the `FluxInstance`, never the namespace:

```bash
kubectl -n flux-system delete fluxinstance flux --timeout=3m
```

The Operator removes Flux's controllers, objects and CRDs. It keeps the namespace, itself, the `ResourceSet` and your workloads.
Afterwards the `ResourceSet` can still show Ready, up to an hour. That only means its last run worked, so check the
`HelmRelease` itself. To bring Flux back, follow steps 3 and 5 above.

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
