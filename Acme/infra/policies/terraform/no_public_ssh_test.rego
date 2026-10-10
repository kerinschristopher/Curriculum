# conftest verify -p Acme/infra/policies/terraform
#
# Each test builds a minimal `terraform show -json` document with one resource change.

package main

import rego.v1

plan(type, after) := {"resource_changes": [{
	"address": sprintf("%s.test", [type]),
	"type": type,
	"change": {"actions": ["create"], "after": after, "after_unknown": {}},
}]}

plan_unknown(type, after, after_unknown) := {"resource_changes": [{
	"address": sprintf("%s.test", [type]),
	"type": type,
	"change": {"actions": ["create"], "after": after, "after_unknown": after_unknown},
}]}

vpc_rule(cidr4, cidr6, proto, from, to) := {
	"cidr_ipv4": cidr4, "cidr_ipv6": cidr6,
	"ip_protocol": proto, "from_port": from, "to_port": to,
}

sg_rule(cidrs, proto, from, to) := {
	"type": "ingress", "cidr_blocks": cidrs, "ipv6_cidr_blocks": null,
	"protocol": proto, "from_port": from, "to_port": to,
}

sg(ingress) := {"ingress": ingress}

inline(cidrs, proto, from, to) := {
	"cidr_blocks": cidrs, "ipv6_cidr_blocks": [],
	"protocol": proto, "from_port": from, "to_port": to,
}

# --- aws_vpc_security_group_ingress_rule ---

test_vpc_rule_ssh_from_world_denied if {
	count(deny) == 1 with input as plan("aws_vpc_security_group_ingress_rule", vpc_rule("0.0.0.0/0", null, "tcp", 22, 22))
}

test_vpc_rule_ssh_from_world_ipv6_denied if {
	count(deny) == 1 with input as plan("aws_vpc_security_group_ingress_rule", vpc_rule(null, "::/0", "tcp", 22, 22))
}

test_vpc_rule_all_protocols_denied if {
	count(deny) == 1 with input as plan("aws_vpc_security_group_ingress_rule", vpc_rule("0.0.0.0/0", null, "-1", null, null))
}

test_vpc_rule_port_range_covering_22_denied if {
	count(deny) == 1 with input as plan("aws_vpc_security_group_ingress_rule", vpc_rule("0.0.0.0/0", null, "tcp", 20, 25))
}

test_vpc_rule_protocol_number_6_denied if {
	count(deny) == 1 with input as plan("aws_vpc_security_group_ingress_rule", vpc_rule("0.0.0.0/0", null, "6", 22, 22))
}

test_vpc_rule_private_cidr_allowed if {
	count(deny) == 0 with input as plan("aws_vpc_security_group_ingress_rule", vpc_rule("10.0.0.0/8", null, "tcp", 22, 22))
}

test_vpc_rule_https_from_world_allowed if {
	count(deny) == 0 with input as plan("aws_vpc_security_group_ingress_rule", vpc_rule("0.0.0.0/0", null, "tcp", 443, 443))
}

test_vpc_rule_range_just_above_22_allowed if {
	count(deny) == 0 with input as plan("aws_vpc_security_group_ingress_rule", vpc_rule("0.0.0.0/0", null, "tcp", 23, 1024))
}

# SSH is TCP only; UDP 22 is not SSH.
test_vpc_rule_udp_22_allowed if {
	count(deny) == 0 with input as plan("aws_vpc_security_group_ingress_rule", vpc_rule("0.0.0.0/0", null, "udp", 22, 22))
}

test_vpc_rule_unknown_cidr_warns_not_denies if {
	p := plan_unknown(
		"aws_vpc_security_group_ingress_rule",
		vpc_rule(null, null, "tcp", 22, 22),
		{"cidr_ipv4": true},
	)
	count(deny) == 0 with input as p
	count(warn) == 1 with input as p
}

test_vpc_rule_unknown_cidr_on_https_no_warning if {
	p := plan_unknown(
		"aws_vpc_security_group_ingress_rule",
		vpc_rule(null, null, "tcp", 443, 443),
		{"cidr_ipv4": true},
	)
	count(warn) == 0 with input as p
}

# --- aws_security_group_rule ---

test_sg_rule_ssh_from_world_denied if {
	count(deny) == 1 with input as plan("aws_security_group_rule", sg_rule(["10.0.0.0/8", "0.0.0.0/0"], "tcp", 22, 22))
}

test_sg_rule_egress_ignored if {
	r := object.union(sg_rule(["0.0.0.0/0"], "-1", 0, 0), {"type": "egress"})
	count(deny) == 0 with input as plan("aws_security_group_rule", r)
}

test_sg_rule_null_cidrs_allowed if {
	count(deny) == 0 with input as plan("aws_security_group_rule", sg_rule(null, "tcp", 22, 22))
}

test_sg_rule_one_unknown_cidr_warns if {
	p := plan_unknown("aws_security_group_rule", sg_rule(["10.0.0.0/8", null], "tcp", 22, 22), {"cidr_blocks": [false, true]})
	count(warn) == 1 with input as p
}

# --- aws_security_group (inline ingress) ---

test_inline_ssh_from_world_denied if {
	count(deny) == 1 with input as plan("aws_security_group", sg([
		inline(["10.0.0.0/8"], "tcp", 443, 443),
		inline(["0.0.0.0/0"], "tcp", 22, 22),
	]))
}

test_inline_all_protocols_denied if {
	count(deny) == 1 with input as plan("aws_security_group", sg([inline(["0.0.0.0/0"], "-1", 0, 0)]))
}

test_inline_null_cidrs_still_checks_ipv6 if {
	rule := object.union(inline(null, "tcp", 22, 22), {"ipv6_cidr_blocks": ["::/0"]})
	count(deny) == 1 with input as plan("aws_security_group", sg([rule]))
}

test_inline_private_allowed if {
	count(deny) == 0 with input as plan("aws_security_group", sg([inline(["10.0.0.0/16"], "tcp", 22, 22)]))
}

# --- Values only known after apply ---
# A real plan leaves an unknown attribute out of `after` (object.remove below) and marks it in after_unknown.

test_inline_whole_ingress_unknown_warns if {
	p := plan_unknown("aws_security_group", {}, {"ingress": true})
	count(deny) == 0 with input as p
	count(warn) == 1 with input as p
}

test_inline_one_rule_unknown_warns if {
	p := plan_unknown("aws_security_group", sg([inline(["10.0.0.0/8"], "tcp", 443, 443), null]), {"ingress": [{}, true]})
	count(deny) == 0 with input as p
	count(warn) == 1 with input as p
}

test_inline_unknown_port_open_to_world_warns if {
	rule := object.remove(inline(["0.0.0.0/0"], "tcp", 22, 22), ["from_port"])
	p := plan_unknown("aws_security_group", sg([rule]), {"ingress": [{"from_port": true}]})
	count(deny) == 0 with input as p
	count(warn) == 1 with input as p
}

test_vpc_rule_unknown_port_open_to_world_warns if {
	p := plan_unknown(
		"aws_vpc_security_group_ingress_rule",
		object.remove(vpc_rule("0.0.0.0/0", null, "tcp", 22, 22), ["from_port"]),
		{"from_port": true},
	)
	count(deny) == 0 with input as p
	count(warn) == 1 with input as p
}

test_vpc_rule_unknown_port_private_cidr_no_warning if {
	p := plan_unknown(
		"aws_vpc_security_group_ingress_rule",
		object.remove(vpc_rule("10.0.0.0/8", null, "tcp", 22, 22), ["from_port", "to_port"]),
		{"from_port": true, "to_port": true},
	)
	count(warn) == 0 with input as p
}

# Unknown ports can't make UDP into SSH.
test_vpc_rule_unknown_port_on_udp_no_warning if {
	p := plan_unknown(
		"aws_vpc_security_group_ingress_rule",
		object.remove(vpc_rule("0.0.0.0/0", null, "udp", 22, 22), ["from_port", "to_port"]),
		{"from_port": true, "to_port": true},
	)
	count(warn) == 0 with input as p
}

test_sg_rule_unknown_protocol_open_to_world_warns if {
	p := plan_unknown("aws_security_group_rule", object.remove(sg_rule(["0.0.0.0/0"], "tcp", 22, 22), ["protocol"]), {"protocol": true})
	count(deny) == 0 with input as p
	count(warn) == 1 with input as p
}

# A missing (unknown) cidr_blocks must not drop the rule.
test_sg_rule_whole_cidr_list_unknown_warns if {
	p := plan_unknown("aws_security_group_rule", object.remove(sg_rule(null, "tcp", 22, 22), ["cidr_blocks"]), {"cidr_blocks": true})
	count(deny) == 0 with input as p
	count(warn) == 1 with input as p
}

# --- Deletes and unrelated resources ---

test_delete_ignored if {
	p := {"resource_changes": [
		{
			"address": "aws_vpc_security_group_ingress_rule.old",
			"type": "aws_vpc_security_group_ingress_rule",
			"change": {"actions": ["delete"], "after": null, "after_unknown": {}},
		},
		{
			"address": "aws_security_group.old",
			"type": "aws_security_group",
			"change": {"actions": ["delete"], "after": null, "after_unknown": {}},
		},
	]}
	count(deny) == 0 with input as p
	count(warn) == 0 with input as p
}

test_other_resource_types_ignored if {
	count(deny) == 0 with input as plan("aws_vpc", {"cidr_block": "0.0.0.0/0"})
}

test_message_names_resource_and_cidr if {
	some msg in deny with input as plan("aws_vpc_security_group_ingress_rule", vpc_rule("0.0.0.0/0", null, "tcp", 22, 22))
	contains(msg, "aws_vpc_security_group_ingress_rule.test")
	contains(msg, "0.0.0.0/0")
}
