# Reference Platform: A Curriculum

A 12-week curriculum for becoming a competent infrastructure automation / platform engineer, structured around building a single portfolio repo that demonstrates Terraform, GitHub Actions, AWS, Kubernetes, GitOps with Flux and Kustomize, and Docker. The capstone repo is something you can pin on GitHub and walk a hiring manager through end-to-end.

## Before you start

Read `FOUNDATIONS.md` and complete the three prep weeks first. They cover environment setup (WSL Ubuntu, minikube, Git), structured reading of *Thinking in Systems* by Donella Meadows, and the everyday Linux fluency the rest of this curriculum assumes. Don't skip them. The systems-thinking vocabulary from the Meadows book (stocks, flows, feedback loops, traps, leverage points) shows up in every module's "systems-thinking lens" section below, and it'll feel like noise if you haven't internalized it.

The local Kubernetes cluster used throughout the curriculum is minikube. Anywhere a tutorial mentions kind or k3d, the same exercises work on minikube with minor adjustments.

## How to use this document

Each module is one week of focused work, roughly 8 to 12 hours. Don't rush. The goal isn't to finish fast, it's to internalize how the pieces fit together so you can answer "why is it built that way?" with conviction.

Three things appear in every module:

- **Concepts.** What you're actually learning. Read these first.
- **Systems-thinking lens.** The connective tissue. The thing most tutorials skip and the thing that separates a Staff-track engineer from someone who just memorized commands.
- **Repo deliverable.** Concrete output that goes into the capstone repo. By week 12 the repo tells a coherent story.

When something doesn't make sense, don't paper over it. Stop, dig, write down what confused you. The notes you take while confused are the notes you'll reference for years.

---

## The capstone repo

A reference implementation of an internal developer platform for a fictional company called Acme. By the end you have:

- Terraform that provisions a VPC, an EKS cluster, IAM, and supporting AWS resources
- A bootstrap process that installs Flux into the cluster
- A Flux-managed `platform/` directory with cluster infrastructure (ingress, cert-manager, monitoring) and applications, each with Kustomize overlays for dev / stage / prod
- Two sample apps with their own Dockerfiles and GitHub Actions pipelines that build, test, scan, and publish container images
- Observability via Prometheus and Grafana, deployed by Flux
- Documentation written for the engineers who'd consume the platform, not just for the person grading you

Target structure:

```
reference-platform/
├── README.md              # the "why"
├── ARCHITECTURE.md        # systems thinking made explicit
├── docs/                  # per-component deep dives
├── infra/
│   ├── terraform/
│   │   ├── modules/       # vpc, eks, iam-roles, etc.
│   │   └── environments/
│   │       ├── dev/
│   │       ├── stage/
│   │       └── prod/
│   └── bootstrap/         # Flux install, initial secrets
├── platform/              # what Flux watches
│   ├── infrastructure/    # ingress-nginx, cert-manager, monitoring
│   │   ├── base/
│   │   └── overlays/
│   └── apps/
│       ├── base/
│       └── overlays/
├── apps/
│   ├── sample-api/        # Go or Python service
│   │   ├── src/
│   │   ├── Dockerfile
│   │   └── .github/workflows/
│   └── sample-web/        # static site or React app
└── .github/workflows/     # platform-level CI (terraform fmt, kustomize build, etc.)
```

The directory names matter. `infra/` is what Terraform owns. `platform/` is what Flux owns. `apps/` is what the application teams own. Three trust boundaries, three different change cadences, three different review cultures. That separation is itself a design decision and you should be able to defend it.

---

## Module 0: Orientation and systems thinking (week 1)

Before any tool, the framing.

### Concepts

A platform is not a pile of infrastructure. It's a product whose users are other engineers. Every platform decision is a tradeoff between what the platform team controls and what the application teams control. Get that boundary wrong in either direction and you fail: too much control and teams route around you, too little and you can't enforce anything (security, cost, reliability).

Read these before touching code:

- *Team Topologies* by Skelton and Pais. Specifically the chapters on platform teams and stream-aligned teams. Skim it fast, then re-read the platform chapter
- *Building Internal Platforms* (Humanitec / CNCF whitepapers, free online). Look for the "platform as a product" framing
- Charity Majors' blog posts on operability. Especially "The Engineer/Manager Pendulum" and her writing on observability as a feedback loop
- Will Larson's *An Elegant Puzzle*, the chapter on systems and platforms

You should already have *Thinking in Systems* under your belt from Foundations. Keep it on your desk. You'll keep referencing it.

### Systems-thinking lens

Four concepts to refer back to constantly. The first three are Meadows' vocabulary applied to platform work. The fourth is a useful adjacent frame.

1. **Stocks, flows, and feedback loops.** Every system in this curriculum has stocks (state files, Git history, container images in a registry, pods running in a cluster) and flows that change them (terraform apply, git push, image build, deployment). Every loop you build is either balancing (drives toward a target, like Kubernetes' reconciliation) or reinforcing (amplifies, like a runaway alert storm).
2. **Trust boundaries.** Every interface between two systems or two teams has an implicit contract. Where are yours? What happens when one side breaks the contract?
3. **Delays.** Meadows: every system has delays, and underestimating them produces oscillation and overshoot. The delay between commit and "in production" is a delay. The delay between "production breaks" and "engineer knows" is a delay. Naming them is the first step to shortening them.
4. **Reversibility.** Two-way vs one-way doors. Most platform decisions feel irreversible because the cost of changing them grows over time. Identify which of your decisions are still cheap to undo and which aren't.

### Repo deliverable

Create the repo. Write `README.md` with one paragraph explaining what Acme Platform is supposed to do for its imaginary engineers. Write `ARCHITECTURE.md` with a single diagram (handdrawn and photographed is fine) showing infra, platform, and apps as three layers and the trust boundaries between them. Don't write the diagram you'd write at the end. Write the one you can defend now. Update it later.

---

## Module 1: Linux, networking, and containers from first principles (week 2)

You can't build a platform on tools you don't understand below the API surface.

### Concepts

- Linux process model: PID namespaces, cgroups, the `/proc` filesystem
- Networking: layers 2, 3, 4, 7. What a route is, what a firewall rule is, what NAT does, what TLS does
- DNS: resolution order, A vs CNAME, what `kubectl exec` doing a DNS lookup actually triggers
- Containers: what a container actually is (it's a process, not a VM). Why `docker run alpine ps` shows what it shows
- Image layers, the build cache, multi-stage builds, distroless and scratch images

### Systems-thinking lens

Containers are an abstraction over Linux primitives that already existed. Kubernetes is an abstraction over containers. Every layer above Linux is a tradeoff: simpler mental model up top, more lost when something breaks underneath. Senior engineers can drop down a layer when the abstraction leaks. Practice dropping down.

### Resources

- *The Linux Programming Interface* by Kerrisk (reference, not cover-to-cover). Use it when you hit something you don't understand
- Julia Evans' zines, particularly "Containers Unplugged" and "How DNS Works"
- Liz Rice's talk *Building a Container From Scratch in Go* on YouTube. Watch it twice
- The official Docker docs on multi-stage builds and BuildKit

### Hands-on

- Write a Dockerfile for a simple Python or Go HTTP service. Get the image under 50 MB
- Use `docker run --rm -it --pid host alpine` and explore the host's process tree from inside the container. Understand what you're seeing
- Run `tcpdump` inside one container while curling a service from another. Read the output

If those last two feel intimidating, that's fine. Do the Dockerfile work first and come back to the namespace exploration after Module 4. You can revisit it.

### Repo deliverable

Build `apps/sample-api/`. Multi-stage Dockerfile producing a small final image. The app itself can be trivial (an HTTP endpoint that returns a JSON health response and a version stamp). Commit the Dockerfile and the source. Don't worry about CI yet.

---

## Module 2: Terraform fundamentals (week 3)

### Concepts

- Providers, resources, data sources
- The state file: what it stores, why it exists, why it's dangerous
- Plan / apply / destroy lifecycle
- Variables and locals, output values
- Implicit vs explicit dependencies (the dependency graph)
- Remote state and state locking (S3 + DynamoDB pattern)

### Systems-thinking lens

The state file is a stock in Meadows' sense. Terraform's plan/apply cycle is the flow that changes it. The cloud is reality. Drift is what happens when the stock and reality diverge. Every Terraform mistake is some flavor of "I forgot the state file thinks something different from reality." Once that clicks, half of Terraform stops being mysterious.

A Terraform module is a function. Inputs (variables), outputs (outputs), side effects (resources). If you wouldn't write a 2,000-line function, don't write a 2,000-line module. If you wouldn't write a function with 40 parameters, don't write a module with 40 variables.

### Resources

- *Terraform Up and Running* by Yevgeniy Brikman. Read chapters 1 through 6
- HashiCorp's official Terraform tutorials at developer.hashicorp.com/terraform/tutorials
- The Terraform AWS provider docs. Bookmark them
- Anton Babenko's `terraform-aws-modules` GitHub org. Read the source of `terraform-aws-vpc` to see what production-grade modules look like

### Hands-on

- Set up an AWS account if you don't have one. Use the free tier carefully
- Write Terraform that creates an S3 bucket and a DynamoDB table to hold remote state, then migrate your own state into them
- Provision a VPC with public and private subnets across two AZs. Don't use a community module yet, write it yourself once so you understand it
- Then refactor to use `terraform-aws-modules/vpc/aws` and notice what you don't have to do anymore

### Repo deliverable

`infra/terraform/modules/vpc/` (your version, even if you'll replace it with a community module later). `infra/terraform/environments/dev/` that calls it. State backend configured. `terraform fmt` and `terraform validate` clean.

---

## Module 3: Terraform composition and AWS depth (week 4)

### Concepts

- Module composition: root modules vs child modules
- Workspaces vs separate state files (and why separate state files usually win)
- Remote modules: pinning versions, semantic versioning of modules
- IAM: principals, policies, trust relationships, roles vs users, OIDC for CI
- AWS networking specifics: VPC endpoints, NAT gateways and their cost, security groups vs NACLs

### When not to Terraform

Terraform is the right tool for provisioning long-lived infrastructure with a clear lifecycle. It is the wrong tool for:

- Ephemeral resources (short-lived test environments are better handled by scripts or controllers that create and destroy on demand)
- Resources with complex internal lifecycle (a Kubernetes Deployment's rolling update is managed by a controller, not Terraform. `terraform apply` would fight the controller)
- Resources that need to react to events (auto-scaling decisions, certificate renewals, DNS failover; these belong to operators and controllers)
- Configuration that changes faster than your apply cycle (feature flags, application config; these belong in ConfigMaps or a config service, not in a state file)

The test: if you'd need to run `terraform apply` more than once a day to keep up with changes, Terraform is probably the wrong layer. Recognize the boundary now so you don't cargo-cult "everything in Terraform" later.

### Systems-thinking lens

The IAM model is the trust model of your cloud. If you can articulate "this service can do these actions on these resources because this role trusts this principal," you're already ahead of most people writing Terraform. If you can't, every IAM error will feel like guesswork.

Pay attention to NAT gateway costs early. They're a frequent source of surprise bills. The fact that your VPC architecture has a cost dimension is itself a systems-thinking moment: there is no purely-technical decision in cloud infrastructure.

### Resources

- AWS Well-Architected Framework, security and cost pillars
- *AWS Certified Solutions Architect Official Study Guide* (you don't need the cert, but the book is a solid AWS reference)
- HashiCorp's tutorial on OIDC with GitHub Actions
- *Terraform Best Practices* by Anton Babenko (free online book)

### Repo deliverable

`infra/terraform/modules/eks/` (or use the community module, but read it). Provision an EKS cluster in your VPC. Set up an IAM role for GitHub Actions to assume via OIDC, scoped to just what your CI needs. Document the IAM trust policy in `docs/iam.md` and explain why each statement is there.

---

## Module 4: Kubernetes fundamentals (week 5)

### Concepts

- The control plane: API server, etcd, controller manager, scheduler. What each one does
- Workload resources: Pods, Deployments, StatefulSets, DaemonSets, Jobs, CronJobs
- Networking: Services (ClusterIP, NodePort, LoadBalancer), Ingress, NetworkPolicies
- Configuration: ConfigMaps, Secrets (and why Kubernetes Secrets aren't really secret)
- RBAC: Roles, ClusterRoles, RoleBindings, ServiceAccounts
- Storage: PersistentVolumes, PersistentVolumeClaims, StorageClasses
- The reconciliation loop. This is the most important concept in Kubernetes, full stop

### Systems-thinking lens

Kubernetes is a control loop engine. Every controller has the same shape: observe desired state, observe actual state, take action to close the gap, repeat. This is exactly Meadows' balancing feedback loop. The desired state is the goal, the actual state is the stock, the controller is the loop.

Once you see this pattern, custom controllers, operators, Flux, ArgoCD, cert-manager, the cluster autoscaler, all stop feeling like separate things. They're the same idea applied to different state.

### Resources

- *Kubernetes in Action* by Marko Lukša, second edition. The best Kubernetes book
- The official tutorials at kubernetes.io/docs/tutorials. Do the "Hello Minikube" and the StatefulSet ones at minimum
- Brendan Burns' talk *The Illustrated Children's Guide to Kubernetes*. Watch it even if it sounds silly, the mental model is good
- *Programming Kubernetes* by Hausenblas and Schwartz, for when you want to write a controller

### Hands-on

- Use the minikube cluster from Foundations. Deploy your sample-api into it as a Deployment with a Service
- Break things on purpose. Delete a pod, watch the controller bring it back. Scale to zero. Set a bad image tag and watch the rollout fail
- Read the events: `kubectl describe pod`, `kubectl get events --sort-by='.lastTimestamp'`. Get fluent

### Repo deliverable

`platform/apps/sample-api/base/` with a Deployment, Service, and ConfigMap. Plain Kubernetes YAML for now, no Kustomize yet. Tested locally on kind.

---

## Module 5: Kustomize and environment management (week 6)

### Concepts

- Bases and overlays
- Strategic merge patches vs JSON 6902 patches
- ConfigMap and Secret generators with hashing for safe rolling updates
- Component composition (the `components` field)
- Common gotchas: namespace handling, cross-cutting changes

### Systems-thinking lens

Kustomize answers a specific question: how do I keep dev, stage, and prod genuinely-similar enough that "it worked in stage" actually means something, while still letting them differ where they must (replica counts, resource limits, image tags, hostnames)? The answer is "express the difference, not the result." Helm answers the same question differently (templating). Kustomize is overlays, Helm is templates. Both have failure modes. Pick deliberately.

If you find yourself patching every field of a base in an overlay, the base is wrong. Push the variability down into the base as a parameter, or split the bases. (Meadows would call the alternative "drift to low performance": every overlay accumulates a few more patches, no individual change is wrong, the system slowly becomes unmaintainable.)

### Resources

- The official Kustomize docs. They're better than they used to be
- The `kustomize` reference at kubectl.docs.kubernetes.io
- Viktor Farcic's YouTube channel for hands-on walkthroughs

### Repo deliverable

Refactor `platform/apps/sample-api/` into `base/` and `overlays/dev/`, `overlays/stage/`, `overlays/prod/`. Differences between overlays should be the actual things you'd want to differ: replica count, resource requests, ingress hostname, log level. Run `kustomize build overlays/dev` and verify the output is what you'd actually want to apply.

Same exercise for `platform/infrastructure/` with at least one component (start with ingress-nginx).

---

## Module 6: GitHub Actions and CI/CD (week 7)

### Concepts

- Workflows, jobs, steps, actions
- Triggers: push, pull_request, workflow_dispatch, schedule
- Matrix builds and reusable workflows
- Secrets management and OIDC (no long-lived AWS keys)
- Artifacts and caching
- Self-hosted runners vs GitHub-hosted runners (when each makes sense)
- Environments and required reviewers for protected deploys

### Systems-thinking lens

A pipeline is a contract between "what counts as a successful change" and "what gets deployed." If your pipeline doesn't run the test suite, "passing CI" doesn't mean "tested." If your pipeline doesn't lint, "merged to main" doesn't mean "follows our standards." Every step you skip is a guarantee you're choosing not to make.

CI is also the most visible delay in your system. Meadows: a balancing loop with a long delay produces oscillation. A pipeline that takes 45 minutes to run gets ignored, which means feedback arrives long after the engineer has context-switched away, which means more bugs ship, which means more emergency fixes, which means more pipeline runs. The slow pipeline causes the busy pipeline. Optimize ruthlessly: caching, parallelization, only running what's needed for what changed.

### Resources

- The official GitHub Actions docs, particularly the security hardening guide
- *GitHub Actions in Action* by Krief, Lippert, and Salgado
- The `actions/cache` README and the various language-specific setup actions
- Read a few well-built workflows: `kubernetes-sigs/kustomize`, `hashicorp/terraform`
- Conftest documentation (conftest.dev) for policy-as-code against Terraform plan JSON
- Terratest by Gruntwork (the README and examples are enough to get started)

### Testing infrastructure code

`terraform validate` and `fmt` check syntax, not behavior. They won't catch a security group that's open to the world or a subnet that can't route to the internet. You need to test what your Terraform actually produces:

- **Policy-as-code (OPA/Conftest or Kyverno):** Write policies that run against `terraform plan` output. Example: "no security group may have ingress from 0.0.0.0/0 on port 22." These run in CI before apply and catch drift from your security posture
- **Integration tests (Terratest or `terraform test`):** Provision real infrastructure in a test account, assert properties (can I reach this endpoint? does this IAM role have the right permissions?), then destroy. Slow and expensive, but the only way to know your Terraform does what you think
- **The minimum viable test:** At bare minimum, run `terraform plan` in CI and assert it's not empty when you expect changes and is empty when you don't. That catches state drift

Don't skip this. A Terraform pipeline that only checks formatting is a pipeline that gives you false confidence.

### Hands-on

- Build a workflow for `apps/sample-api/` that lints, tests, builds the container, scans it with Trivy, and pushes to GHCR on main
- Build a workflow for the Terraform that runs `fmt`, `validate`, `plan` on PRs, comments the plan back to the PR, and applies on merge to main with required reviewer approval
- Add a Conftest or OPA step to the Terraform workflow that fails on security policy violations (start with one rule: no public SSH access)
- Wire up OIDC so neither workflow uses long-lived AWS credentials

### Repo deliverable

`.github/workflows/sample-api-ci.yml`, `.github/workflows/terraform-plan.yml`, `.github/workflows/terraform-apply.yml`. All using OIDC. At least one Conftest policy in `infra/policies/` that validates Terraform plan output. Document the IAM role each workflow assumes in `docs/ci.md`.

---

## Module 7: Docker-in-Docker and test pipelines (week 8)

### Concepts

- Why DinD exists (container-based test runners that need to build containers)
- The privileged-flag tradeoff and the rootless alternatives
- BuildKit features: cache mounts, secrets, build args
- Multi-architecture builds and `docker buildx`
- Layer caching strategies in CI: registry cache, GHA cache, mount cache

### Systems-thinking lens

This module is about a specific feedback-loop optimization. A test pipeline that has to rebuild the world every run is a pipeline that gets skipped. Caching is not a nice-to-have, it's the difference between a test culture and a "we'll fix it after release" culture. Notice how much of platform engineering is about making the right thing also the fast thing.

### Resources

- The Moby project's BuildKit docs
- The "Building containers in Kubernetes without privileges" blog post family (Kaniko, Buildah, BuildKit rootless)
- The `docker/build-push-action` README, in particular the cache section

### Repo deliverable

Update `apps/sample-api/`'s CI to use BuildKit cache. Add a `apps/sample-api/Dockerfile.test` that runs the test suite in a container, and a workflow that invokes it. Time the cold-cache vs warm-cache runs and put the numbers in `docs/ci.md`. The numbers are the point.

---

## Module 8: GitOps with Flux (week 9)

### Concepts

- Pull vs push deployment models, and why pull wins for clusters at scale
- Flux's controllers: source-controller, kustomize-controller, helm-controller, notification-controller, image-automation-controller
- The bootstrap process: how Flux installs itself and how it survives a cluster wipe
- Reconciliation interval and what it means for change latency
- Drift detection: what happens when someone `kubectl apply`s something Flux didn't put there
- Image automation: Flux watching a registry and updating Git when new images land

### Systems-thinking lens

GitOps is the balancing-feedback-loop pattern from module 4 applied to deployment. The cluster's actual state is the stock. The Git repo is the desired state. Flux is the loop that closes the gap. The implications:

- The Git repo is the audit log. Every change is a commit, every revert is a commit
- A bad change is rolled back by reverting the commit, not by SSHing to a cluster
- The cluster is no longer special. It can be destroyed and rebuilt and Flux will pull it back to the desired state

This last point is what makes the fleet-management story possible. Five clusters, fifty clusters, five hundred clusters. The platform engineer's job stops scaling linearly with cluster count. In Meadows' terms, you've moved up the leverage-points hierarchy: instead of changing parameters (kubectl apply) you've changed the structure of the system itself (declarative state, reconciled).

### Resources

- The official Flux documentation, particularly the "core concepts" and "guides" sections
- *GitOps and Kubernetes* by Yuen, Matyushentsev, Ekenstam, and Suen (Manning)
- The `fluxcd/flux2-kustomize-helm-example` repo. Read the structure, then read the Flux configs
- Stefan Prodan's blog (Flux maintainer)

### Hands-on

- Bootstrap Flux into your kind cluster pointing at your repo's `platform/` directory
- Make a change to a Kustomize overlay, push it, watch Flux apply it
- `kubectl edit` something Flux manages, watch Flux revert it
- Add the image-automation controllers and have Flux update `sample-api`'s image tag automatically when CI publishes a new version

### Repo deliverable

`infra/bootstrap/` with the Flux install manifests. `platform/` structured so Flux watches `platform/infrastructure/overlays/${env}` and `platform/apps/overlays/${env}` for the cluster's environment. Document the bootstrap process in `docs/bootstrap.md` such that a new engineer could rebuild the cluster from scratch by following it.

---

## Module 9: Observability (week 10)

### Concepts

- The three pillars (metrics, logs, traces) and why "three pillars" is a slightly misleading framing
- Prometheus: pull-based metrics, the data model (labels), recording rules, alerting rules
- Grafana: dashboards as code (provisioning), data sources
- The kube-prometheus-stack Helm chart: what it gives you, what it doesn't
- SLIs, SLOs, and error budgets at a conceptual level
- The difference between monitoring (watching for known failure modes) and observability (asking new questions about your system)

### Systems-thinking lens

Observability is the feedback loop from production back to engineers. Without it, every other piece of this platform is a leap of faith. A dashboard that nobody looks at is worse than no dashboard, because it implies a feedback loop that doesn't exist. Build dashboards that someone will actually use to answer a question they actually have.

Charity Majors' line is the right one: you don't know your system is observable until you've used it to debug something you didn't predict.

### Resources

- *Observability Engineering* by Majors, Fong-Jones, and Miranda
- The Prometheus and Grafana official docs
- Brendan Gregg's USE method writeup
- Google's SRE book, the chapters on monitoring and SLOs

### Repo deliverable

`platform/infrastructure/base/monitoring/` deploying kube-prometheus-stack via Flux's HelmRelease. Two Grafana dashboards provisioned as ConfigMaps: one for cluster health, one for sample-api specifically. A Prometheus alerting rule that fires when sample-api's error rate goes above some threshold. Document what the dashboards are for in `docs/observability.md`, written for the on-call engineer who doesn't yet exist.

---

## Module 10: Security and secrets (week 11)

### Concepts

- The Kubernetes secrets problem: base64-encoded etcd entries are not actually secret
- External Secrets Operator and the SecretStore pattern
- AWS Secrets Manager or SSM Parameter Store as a backing store
- Sealed Secrets as an alternative
- Container image scanning (Trivy, Grype)
- Network policies as a default-deny posture
- Pod security standards (restricted, baseline, privileged)

### Systems-thinking lens

Security is a property of the whole system, not a feature you add at the end. Every trust boundary you identified in module 0 has a security dimension. The IAM role in module 3 is security. The OIDC trust in module 6 is security. The image scan in module 7 is security. The secrets pattern in this module is security.

The temptation is to bolt on security tools. Resist it. Look at where trust crosses a boundary in your design and ask: what's enforcing the contract there?

### Resources

- *Container Security* by Liz Rice
- The CNCF security whitepaper
- The External Secrets Operator docs
- The Pod Security Standards page on kubernetes.io

### Repo deliverable

External Secrets Operator deployed via Flux. Sample-api consuming a secret sourced from AWS Secrets Manager via an ExternalSecret. NetworkPolicy in `platform/apps/sample-api/base/` denying all ingress except from the ingress controller's namespace. Trivy scan step in CI failing the build on HIGH or CRITICAL findings.

---

## Module 11: Capstone integration and documentation (week 12)

This week you don't add features. You make the existing thing coherent.

### What to do

Read your repo as if you'd never seen it. Where does it fail to explain itself? Fix that.

- Walk through the `README.md` and rewrite it for an engineer who's about to evaluate the repo in 90 seconds. Lead with what it is, then a quickstart, then a pointer to `ARCHITECTURE.md`.
- `ARCHITECTURE.md` should now have a real diagram. Use Excalidraw or draw.io. Show the trust boundaries. Show what owns what.
- Each `docs/*.md` should answer one question. Name them after the question, not the topic. `docs/why-flux-not-argo.md` is more useful than `docs/gitops.md`.
- Add a `docs/decisions/` directory with at least three ADRs (Architecture Decision Records). One on Terraform module structure, one on GitOps tool choice (Flux vs ArgoCD), one on Kustomize vs Helm. Use the standard ADR template (Status / Context / Decision / Consequences). The GitOps ADR is the most important: you need to defend choosing Flux against a hypothetical team that wants Argo. What does Argo give you that Flux doesn't? What does Flux give you that Argo doesn't? When would you change your mind? If you can't articulate the tradeoff, you haven't chosen Flux; you've just used it because the curriculum told you to.
- Write a `CONTRIBUTING.md` aimed at the imaginary application engineer onboarding to the platform. What's their first commit going to be? Make that path obvious.

### Systems-thinking lens

Documentation is part of the product. The engineers who use the platform read documentation, not source code. If your documentation is inconsistent, your platform feels inconsistent regardless of how clean the YAML is. The point of writing it now, at the end, is that you can write it from the perspective of someone who's already used the platform, which is the perspective that produces the best docs.

### What "done" looks like

- Someone unfamiliar with the repo can clone it, follow the quickstart, and have a running dev environment in 30 minutes
- Every architectural decision in the repo has a documented rationale
- The README answers "what is this and why would I care" in the first paragraph
- Pinning this repo on your GitHub profile feels accurate, not aspirational

---

## Standing resources

These belong on a permanent reading shelf, not a single module.

**Books**

- *Team Topologies*, Skelton and Pais
- *The Phoenix Project*, Kim, Behr, and Spafford. (Skip *The DevOps Handbook* if you only have time for one.)
- *Accelerate*, Forsgren, Humble, and Kim
- *Site Reliability Engineering* and *The Site Reliability Workbook*, both free from Google online
- *Designing Data-Intensive Applications*, Kleppmann. Not platform-specific but it'll change how you think about systems
- *Software Engineering at Google*, free online

**People to follow**

Charity Majors, Liz Rice, Kelsey Hightower, Lorin Hochstein, Tanya Reilly, Will Larson, Lara Hogan. Their long-form writing is more valuable than any single course.

**Communities**

- Kubernetes Slack (`kubernetes.slack.com`). Join `#flux`, `#kustomize`, `#sig-cluster-lifecycle`
- The CNCF YouTube channel for KubeCon talks
- r/kubernetes and r/devops on Reddit, with a tolerance for noise
- The HashiCorp Discuss forum for Terraform-specific questions

---

## Assessment

You're ready to put this on a resume when you can:

1. Walk a stranger through the architecture diagram in five minutes
2. Defend three decisions in the repo against "why didn't you do it the other way"
3. Pull up your dashboards and explain what each panel is for and how you'd debug a regression with them
4. Deliberately break the cluster (delete the Flux namespace, for example) and recover it
5. Add a new application to the platform end-to-end (Terraform IAM, GitHub Actions CI, Flux deployment, monitoring) in under an afternoon

If any of those feels shaky, that's where to spend the next week.

---

## A note on systems thinking

The technical content of this curriculum is a year or two of solid work. The systems-thinking content is a career. Notice what threads through every module:

Stocks and flows (what accumulates, what changes it). Feedback loops (balancing or reinforcing, with delays that matter). Trust boundaries (who owns what, who trusts what). Leverage points (where in the system intervention is cheapest). Reversibility (how cheap is it to change my mind).

Those are Meadows' lenses, plus two practical ones. When you're learning a new tool five years from now, the same lenses will still be the right ones to look through. The tool will change. The questions don't.
