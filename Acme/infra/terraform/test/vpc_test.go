//go:build integration

// Package test holds Terratest integration tests: they build Terraform modules in real AWS, check
// the result through the AWS API, and destroy everything at the end. The integration build tag
// keeps a plain `go test ./...` from creating anything. Run from WSL, as a human with AWS
// credentials (never in CI):
//
//	cd Acme/infra/terraform/test && go test -tags integration -v -timeout 30m ./...
//
// The module's logic is also unit-tested with a mocked provider (modules/vpc/tests/); this test
// adds what a mock can't show: that AWS accepts the module and the routing really works.
package test

import (
	"context"
	"fmt"
	"strings"
	"testing"

	awssdk "github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/service/ec2"
	ec2types "github.com/aws/aws-sdk-go-v2/service/ec2/types"
	"github.com/gruntwork-io/terratest/modules/aws"
	"github.com/gruntwork-io/terratest/modules/random"
	"github.com/gruntwork-io/terratest/modules/terraform"
	teststructure "github.com/gruntwork-io/terratest/modules/test-structure"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

const region = "us-east-1"

func TestVpcModule(t *testing.T) {
	t.Parallel()

	// Copy the whole Terraform tree to a temp dir so the fixture's relative module source still
	// resolves and its local state never lands in the repo.
	dir := teststructure.CopyTerraformFolderToTemp(t, "..", "test/fixtures/vpc")
	name := "tt-" + strings.ToLower(random.UniqueId())

	opts := terraform.WithDefaultRetryableErrors(t, &terraform.Options{
		TerraformDir: dir,
		Vars:         map[string]interface{}{"name": name, "region": region},
		NoColor:      true,
	})

	// Registered first so it runs even when an assertion below fails the test.
	defer terraform.Destroy(t, opts)
	terraform.InitAndApply(t, opts)

	vpcID := terraform.Output(t, opts, "vpc_id")
	public := terraform.OutputList(t, opts, "public_subnet_ids")
	private := terraform.OutputList(t, opts, "private_subnet_ids")

	t.Run("vpc carries the caller's tags", func(t *testing.T) {
		tags := aws.GetTagsForVpc(t, vpcID, region)
		assert.Equal(t, "terratest", tags["Environment"])
		assert.Equal(t, name, tags["Name"])
	})

	t.Run("one public and one private subnet per zone", func(t *testing.T) {
		subnets := aws.GetSubnetsForVpc(t, vpcID, region)
		require.Len(t, subnets, 4)
		require.Len(t, public, 2)
		require.Len(t, private, 2)

		zones := map[string]int{}
		for _, s := range subnets {
			zones[s.AvailabilityZone]++
		}
		assert.Equal(t, map[string]int{region + "a": 2, region + "b": 2}, zones)
	})

	// IsPublicSubnet asks AWS whether the subnet's route table sends 0.0.0.0/0 to an internet gateway.
	t.Run("public subnets reach the internet, private ones don't", func(t *testing.T) {
		for _, id := range public {
			assert.True(t, aws.IsPublicSubnet(t, id, region), "public subnet %s has no route to the internet gateway", id)
		}
		for _, id := range private {
			assert.False(t, aws.IsPublicSubnet(t, id, region), "private subnet %s routes to the internet gateway", id)
		}
	})

	t.Run("no NAT gateway with enable_nat_gateway = false", func(t *testing.T) {
		out, err := aws.NewEc2Client(t, region).DescribeNatGateways(context.Background(), &ec2.DescribeNatGatewaysInput{
			Filter: []ec2types.Filter{{Name: awssdk.String("vpc-id"), Values: []string{vpcID}}},
		})
		require.NoError(t, err)
		var live []string
		for _, n := range out.NatGateways {
			if n.State != ec2types.NatGatewayStateDeleted {
				live = append(live, fmt.Sprintf("%s (%s)", awssdk.ToString(n.NatGatewayId), n.State))
			}
		}
		assert.Empty(t, live, "NAT gateways are billed by the hour")
	})
}
