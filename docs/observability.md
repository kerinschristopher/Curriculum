# Observability: what's watched, and what to do when the alert fires

What the cluster collects about `sample-api`, how to look at it, and what to do when `SampleApiHighErrorRate` fires. It's
written for whoever is on call. Run everything from WSL, with `kubectl` on `kind-sample-api`. How the cluster gets its
monitoring is in [`docs/bootstrap.md`](bootstrap.md); the design and its evidence are in
[`docs/design/module-9-observability.md`](design/module-9-observability.md).

## What's collected

**Metrics only.** Nothing collects logs or traces. A pod's logs are there through `kubectl logs` while the pod exists, and
are gone with it.

One Prometheus, in the `monitoring` namespace, watches all three environments, because they share one cluster. Every
`sample-api` series carries a `namespace` label, `sample-api-dev`, `sample-api-stage` or `sample-api-prod`: that's how you
tell the environments apart. Prometheus scrapes every 30 seconds:

| Source | What it tells you |
|---|---|
| `sample-api`'s own metrics (port `8081`, `/metrics`) | Every request: how long it took, with its `route`, `method` and status `code`. `route` is the pattern that served it (`/health`, `/fault`) or `unmatched`. Also Go's own numbers: goroutines, heap, garbage collection |
| kube-state-metrics | What Kubernetes thinks: pods ready or not, restarts, replicas wanted, requests and limits, which image each pod runs |
| The kubelet (cAdvisor) | What each container really uses: CPU, memory, CPU throttling |
| node-exporter | The node: CPU, load, memory, disk space, network |
| The monitoring parts themselves | Prometheus, Alertmanager, Grafana, the operator, CoreDNS and the API server |

Not watched: kind's controller manager, scheduler, etcd and kube-proxy (kind hides their metrics, so they're switched off),
and ingress-nginx (its metrics are off).

On kind, Prometheus keeps **2 days** of history, in the pod's own storage. A new Prometheus pod starts with none.

## Getting in

Grafana: follow [Open Grafana](bootstrap.md#open-grafana). Prometheus and Alertmanager have their own pages; each of these
runs until you press Ctrl-C, so give each its own terminal:

```bash
kubectl -n monitoring port-forward svc/kube-prometheus-stack-prometheus 9090:9090
```

```bash
kubectl -n monitoring port-forward svc/kube-prometheus-stack-alertmanager 9093:9093
```

Then open http://localhost:9090/alerts (every alert rule, and which are pending or firing) and http://localhost:9093
(Alertmanager: the alerts that are firing). A port-forward listens on your machine only.

## When `SampleApiHighErrorRate` fires

### What it measures

The alert compares two numbers for each environment, over the last 5 minutes: requests that returned a 5xx, and all
requests. If more than 5% failed, and that stays true for 5 minutes, it fires, with the environment in its `namespace`
label. The kubelet's `/health` probes are left out of both numbers: they always succeed, so counting them would only
dilute the ratio, and prod's three pods would dilute it three times as much as dev's one.

**Why 5%.** The number that matters is an **SLO**, a target: 99% of requests succeed, measured over 28 days. The 1% that
may fail is the **error budget**, about 6.7 hours of total failure in 28 days. Failing 5% of requests spends that budget
five times as fast as allowed, so it would be gone in under 6 days. The alert is a rough alarm for fast spending: it
catches a bad outage within minutes, and misses a slow leak of 2-3% that would also empty the budget. (The better alarm,
which measures how fast the budget is spent over two windows, is future work in the design doc.)

**How long it takes.** At 10% errors, Prometheus marks it **pending** about a minute after the errors start and
**firing** 5 minutes later. It stops firing once the last 5 minutes are back under 5%, or have no requests at all.

### Where you see it

**Nobody is paged.** Alertmanager has no receiver (no Slack, email or pager is set up yet), so a firing alert shows only
where you look: Prometheus's Alerts page, Alertmanager's page, or this block, which lists every alert that's pending or
firing:

```bash
(
set -eu
kubectl -n monitoring port-forward svc/kube-prometheus-stack-prometheus 19090:9090 >/dev/null &
pf=$!
trap 'kill "$pf"' EXIT
timeout 20 sh -c 'until curl -fs -o /dev/null http://localhost:19090/-/ready; do sleep 1; done' \
  || { echo "The port-forward wasn't ready after 20 s; stopping"; exit 1; }
curl -fsS http://localhost:19090/api/v1/query --data-urlencode 'query=ALERTS' \
  | python3 -c 'import json, sys
for r in json.load(sys.stdin)["data"]["result"]:
    m = r["metric"]; print(m["alertstate"], m["alertname"], m.get("namespace", ""))'
)
```

`firing Watchdog` is always there; see [Things that look wrong but aren't](#things-that-look-wrong-but-arent).

### First steps

1. **Which environment?** Read the alert's `namespace`. Today only dev returns a 5xx on purpose (its `/fault` route), so
   in stage or prod the alert has never fired: take it seriously there.
2. **Open [the sample-api dashboard](#the-sample-api-dashboard)** and pick that environment at the top. Then, top
   right, set the time range to the last 30 minutes, and the refresh button's drop-down to 30s. On the default 6 hours a
   problem that started minutes ago is a sliver at the right-hand edge, and the dashboard doesn't refresh by itself.
3. **Error ratio** (panel 2): when did it cross the red line, and is it still above it?
4. **Traffic by status code** (panel 1): which codes, and how much traffic? Five errors in a quiet minute can be 50%.
5. **Version per pod** (panel 4): did a new version arrive when the errors started? If it did, roll back: see "A bad
   release is deployed" in [Rolling back](ci.md#rolling-back).
6. **Pods ready vs wanted** and **Restarts per pod** (panels 5-6): are pods crashing or not ready?
7. **CPU throttling** and **Memory used vs limit** (panels 8-9): is a pod held back at its CPU limit, or killed at its
   memory limit? A container killed for memory shows as a restart in panel 6.
8. **Which route?** In Explore, run "5xx by route" from [Explore starters](#explore-starters). Then read the pods' logs:
   `kubectl -n sample-api-prod logs deploy/sample-api` (with the environment's namespace).

### Make it fire on dev

To practise, or to check the alert works. This sends one `/fault` (a 500) and one `/` (a 404, which isn't an error here)
every second for 7 minutes, so half the requests fail:

```bash
(
set -eu
kubectl -n sample-api-dev port-forward svc/sample-api 18080:80 >/dev/null &
pf=$!
trap 'kill "$pf"' EXIT
timeout 20 sh -c 'until curl -fs -o /dev/null http://localhost:18080/health; do sleep 1; done' \
  || { echo "The port-forward wasn't ready after 20 s; stopping"; exit 1; }
code=$(curl -s -o /dev/null -w '%{http_code}' http://localhost:18080/fault)
[ "$code" = 500 ] || { echo "/fault answered $code, not 500; stopping"; exit 1; }
end=$(( $(date +%s) + 420 ))
while [ "$(date +%s)" -lt "$end" ]; do
  curl -s -o /dev/null http://localhost:18080/fault
  curl -s -o /dev/null http://localhost:18080/
  sleep 1
done
echo "Done: 7 minutes at 50% errors on dev"
)
```

In another terminal, run the `ALERTS` block above now and then: `pending SampleApiHighErrorRate sample-api-dev` within a
minute or two, `firing` about 5 minutes later, then gone about 5 minutes after the loop ends. Stage and prod have no
`/fault` route (only dev's overlay turns it on), so the same block there stops at the check.

## The sample-api dashboard

In Grafana: Dashboards, then `sample-api` (or http://localhost:3000/d/sample-api). It opens on dev, over the last 6 hours.
**Environment**, at the top, picks one environment at a time; that's on purpose, because a ratio over all three would hide
a failing prod behind dev's practice faults.

| # | Panel | The question it answers | Read it like this |
|---|---|---|---|
| 1 | Traffic by status code | How many requests, and how many fail? | Requests per second, stacked by code. Counts the `/health` probes, so a quiet environment still shows a line |
| 2 | Error ratio (alert fires above 5% for 5 min) | Is the alert's condition true? | The alert's own expression, over the same fixed 5 minutes, so panel and alert agree. The red line is 5% |
| 3 | Latency (p50, p95, p99) | How slow are requests? | Leaves out `/health`, whose fast answers would hide a slow route. See the note below the table |
| 4 | Version per pod | What runs where? | `image_spec` is the tag and digest from the Deployment, as Flux set it |
| 5 | Pods ready vs wanted | Is every replica serving? | `wanted` is the Deployment's replicas, `ready` what passes readiness. A gap during a rollout is normal |
| 6 | Restarts per pod | Is a pod crashing? | Each step up is one restart. A new pod starts again at 0 |
| 7 | CPU used vs request and limit | Is CPU close to the limit? | Cores per pod; the dashed lines are the request and the limit |
| 8 | CPU throttling | Is the limit holding the pod back? | The share of CPU time slices the kernel held back. Anything well above 0 means the limit is too low for the load |
| 9 | Memory used vs limit | Will a pod be killed for memory? | At the limit the kernel kills the container, which shows in panel 6 |
| 10 | Goroutines per pod | Is something piling up inside the app? | A steady climb that never falls back is a leak |
| 11 | Go heap in use per pod | Is memory leaking inside the app? | Same reading as panel 10 |

**Latency on an idle app is fiction.** With almost every request finishing in under 5 ms, panel 3 shows exactly 2.50,
4.75 and 4.95 ms. Prometheus only knows which bucket a request fell in (the first ends at 5 ms), and spreads them evenly
across it. Only a change of bucket means something. The last bucket ends at 2.5 s, so above that every line stops at
2.5 s, however slow requests get.

For scale, idle on 2026-11-10: a prod pod used about 0.0002 cores (request 0.2, limit 0.5) and 7 MiB (limit 256 MiB);
dev ran 22 goroutines with a 3.3 MiB heap.

## Cluster health: "Node Exporter / Nodes"

The monitoring chart installs 25 dashboards. For the cluster's health use **Node Exporter / Nodes** (search for it under
Dashboards). Pick the node under **Instance**; kind has one.

- **CPU Usage** and **Load Average**: is the node short of CPU? Load above the "logical cores" line means work is waiting.
- **Memory Usage** (graph and gauge): is it short of memory? The gauge turns orange at 80% and red at 90%.
- **Disk Space Usage**: is a disk filling? One row per mount, with "Used, %".
- **Disk I/O** and **Network Received / Transmitted**: for digging, not for a first look.

On kind the node is a container inside WSL2's virtual machine, so these numbers are the whole VM's: Docker Desktop and
everything else running there are in them too.

**Pods not Ready or restarting** aren't on any of the 25 dashboards. Two of the chart's alerts cover them, for every
namespace: `KubePodNotReady` (a pod not ready for 15 minutes) and `KubePodCrashLooping` (a container stuck restarting).
To look before they fire, use "Pods not ready" in [Explore starters](#explore-starters); for `sample-api`, panels 5 and 6.

The other 24 dashboards (for example the "Kubernetes / Compute Resources" set) are there for digging, but aren't part of
the on-call path.

## Explore starters

In Grafana, open Explore, keep the Prometheus data source, paste a query and run it. Some of these (`ALERTS`, pods not
ready, the version) return series whose value is always 1, so their lines lie on top of each other: the answer is in
their labels. The table under the graph lists them as they are now; for earlier ones, hover over the graph.

| Query | Answers |
|---|---|
| `sum by (namespace) (rate(http_request_duration_seconds_count{job="sample-api", route!="/health", code=~"5.."}[5m])) / sum by (namespace) (rate(http_request_duration_seconds_count{job="sample-api", route!="/health"}[5m]))` | The alert's ratio, every environment at once |
| `sum by (namespace, route, code) (rate(http_request_duration_seconds_count{job="sample-api", code=~"5.."}[5m]))` | 5xx by route: which route is failing |
| `ALERTS{alertstate="firing", alertname!="Watchdog"}` | Everything firing, apart from `Watchdog` (always firing, so its line would hide the rest). Empty when all is well |
| `up == 0` | Anything Prometheus can't scrape |
| `kube_pod_status_ready{condition="false"} == 1` | Pods not ready, in any namespace. Short blips from `ingress-nginx-admission-create` and `-patch` are ingress-nginx's one-off setup jobs, which Flux re-runs about every 10 minutes: not a problem |
| `changes(kube_pod_container_status_restarts_total[1h]) > 0` | Containers that restarted in the last hour |
| `max by (namespace, pod, image_spec) (kube_pod_container_info{container="sample-api"})` | Which version runs where, every environment at once |

## Things that look wrong but aren't

- **`Watchdog` is always firing.** It's meant to be: it proves the path from Prometheus to Alertmanager works. Worry when
  it's missing.
- **"No data" on Error ratio or Latency.** A series only exists once a request with that route and code has happened.
  Stage and prod have never returned a 5xx, so their error ratio shows "No data", not 0%, even with real traffic. Both
  panels also leave `/health` out, so with only probes coming in they have nothing to count. Traffic by status code still
  shows the probes.
- **Latency of exactly 2.50, 4.75 and 4.95 ms.** Buckets, not timings; see [the dashboard](#the-sample-api-dashboard).
- **No `AlertmanagerClusterCrashlooping` on kind.** It's switched off there. WSL2 keeps stepping the VM's clock, so every
  process's recorded start time moves at nearly every scrape, and this alert, which counts those moves, fired for good
  with nothing restarting. The same goes for any query on `process_start_time_seconds` on kind: count restarts with
  `kube_pod_container_status_restarts_total` instead. `KubePodCrashLooping` still works.
- **Grafana restarted once after a monitoring change.** At the first Helm upgrade after a fresh install, Grafana's pod is
  replaced once, although nothing in it changed but a checksum. Later upgrades don't do it. The likely cause, unproved: on
  the first install the chart's checksum is worked out from a random password, which then differs once.
- **A new `sample-api` pod's target shows "unknown" for up to 30 seconds**, until its first scrape. A new alert rule or
  monitor also takes about 30 seconds to appear after Flux applies it.
- **Alerts pending briefly while the cluster starts** (`KubePodNotReady`, `KubeDeploymentReplicasMismatch`,
  `KubeStatefulSetReplicasMismatch`). They clear before their wait ends and never fire.
