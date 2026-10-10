# Terraform plan policies (Conftest)

Rules every Terraform plan must pass before it can be applied. CI runs them on every plan,
including plans with no changes: the PR plan and the post-merge plan both go through
`.github/workflows/terraform-plan-reusable.yml`, and the policy job in `terraform-plan.yml` runs the tests.

| File | What it is |
|---|---|
| `no_public_ssh.rego` | `deny`: no security group rule may allow SSH (TCP 22, or all protocols) from `0.0.0.0/0` or `::/0`. `warn`: such a rule's CIDR is only known after apply, so it can't be checked |
| `no_public_ssh_test.rego` | Unit tests (`conftest verify`) on small hand-written plans |
| `testdata/seeded-bad/` | A real plan of `main.tf`, which opens SSH in each of the three resource shapes and has one CIDR unknown until apply. CI requires exactly 3 denies and 1 warning from it |

`deny` fails the plan; `warn` is printed and the plan passes.

## Run locally (WSL)

```sh
# The policy's tests
conftest verify -p Acme/infra/policies/terraform

# A real plan
cd Acme/infra/terraform/environments/dev
terraform plan -lock=false -out=tfplan
terraform show -json tfplan > plan.json
conftest test plan.json -p ../../../policies/terraform
```

`tfplan` and `plan.json` contain values Terraform redacts elsewhere: delete them afterwards, and
don't commit them.

## Regenerate the seeded fixture

Only needed if the AWS provider changes its plan JSON. Plan only; never apply it.

```sh
cd Acme/infra/policies/terraform/testdata/seeded-bad
terraform init && terraform plan -out=p.tfplan && terraform show -json p.tfplan > full.json
# Keep only format_version, terraform_version and resource_changes, and check that it holds
# no account ID, user name or IP before committing it as plan.json.
```
