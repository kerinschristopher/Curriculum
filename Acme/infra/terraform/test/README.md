# Terraform integration tests (Terratest)

Tests that build a Terraform module in **real AWS**, check the result through the AWS API, and destroy everything at the end.
They complement the module unit tests (`terraform test` with a mocked provider, in `modules/<name>/tests/`), which run in CI
on every Terraform change but can't show that AWS accepts the module or that routing really works.

| Test | Builds | Checks | Cost and time |
|---|---|---|---|
| `TestVpcModule` (`vpc_test.go`) | `modules/vpc` through `fixtures/vpc`, with NAT off, named `tt-<random>`, tagged `ManagedBy = terratest` | the tags; one public and one private subnet per zone; public subnets route `0.0.0.0/0` to the internet gateway and private ones don't; no NAT gateway | 13 resources, none billed by the hour; about 40 s |

## Run (WSL, as a human with AWS credentials)

```sh
cd Acme/infra/terraform/test
go test -tags integration -v -timeout 30m ./...
```

- **The `integration` build tag** keeps a plain `go test ./...` from creating anything.
- **The test copies the whole Terraform tree to a temp directory**, so its local state never lands in the repo.
- **The destroy is deferred**, so it runs even when a check fails. If the process is killed mid-run, find the leftovers with
  `aws ec2 describe-vpcs --filters Name=tag:ManagedBy,Values=terratest` and delete them.

## Why it isn't in CI (yet)

It needs AWS **write** access. The CI roles deliberately don't give PR code any: the plan role is read-only, and the apply role
is only issued after a human approves `dev-apply`. Running this in CI needs its own role (create and delete only resources
tagged `ManagedBy = terratest`) and a trigger restricted to `main` or manual runs. That's a separate design.

## Evidence (2026-10-10)

- **Pass:** 13 added, 4 checks passed, 13 destroyed (40 s).
- **Mutation:** a temp copy with the public subnets on the private route table failed only the routing check
  (`public subnet … has no route to the internet gateway`). The deferred destroy still removed all 13 resources, and nothing
  tagged `ManagedBy = terratest` was left.
