# No security group may open SSH (TCP port 22) to the whole internet (0.0.0.0/0 or ::/0).
#
# Input: `terraform show -json <planfile>`. Every resource the plan will exist with afterwards is
# checked (create, update, replace and unchanged "no-op"), so an existing violation fails too.
# Deletes have change.after = null and so never match.
#
# A plan describes an ingress rule in three shapes, depending on the AWS provider resource:
#   aws_security_group                   inline ingress[]: cidr_blocks, ipv6_cidr_blocks, protocol
#   aws_security_group_rule              type = "ingress": cidr_blocks, ipv6_cidr_blocks, protocol
#   aws_vpc_security_group_ingress_rule  cidr_ipv4, cidr_ipv6, ip_protocol
# All three use from_port/to_port; protocol "-1" (or "all") means every protocol and port.
#
# A CIDR known only after apply (e.g. taken from another resource) can't be checked: that is a
# warn, not a deny, so the plan still passes but the reader is told to look.

package main

import rego.v1

world := {"0.0.0.0/0", "::/0"}

all_protocols := {"-1", "all"}

tcp := {"tcp", "6"}

# Does a rule with this protocol and port range let SSH through?
allows_ssh(proto, _, _) if proto in all_protocols

allows_ssh(proto, from, to) if {
	proto in tcp
	from <= 22
	to >= 22
}

# --- One entry per ingress rule, whatever its shape: {address, cidrs, unknown, proto, from, to} ---

rules contains rule if {
	some rc in input.resource_changes
	rc.type == "aws_security_group"
	some i, ing in rc.change.after.ingress
	rule := {
		"address": sprintf("%s ingress[%d]", [rc.address, i]),
		"cidrs": array.concat(nulls_to_empty(object.get(ing, "cidr_blocks", [])), nulls_to_empty(object.get(ing, "ipv6_cidr_blocks", []))),
		"unknown": inline_cidrs_unknown(rc, i),
		"proto": ing.protocol,
		"from": ing.from_port,
		"to": ing.to_port,
	}
}

rules contains rule if {
	some rc in input.resource_changes
	rc.type == "aws_security_group_rule"
	after := rc.change.after
	after.type == "ingress"
	rule := {
		"address": rc.address,
		"cidrs": array.concat(nulls_to_empty(after.cidr_blocks), nulls_to_empty(after.ipv6_cidr_blocks)),
		"unknown": any_unknown(rc, ["cidr_blocks", "ipv6_cidr_blocks"]),
		"proto": after.protocol,
		"from": after.from_port,
		"to": after.to_port,
	}
}

rules contains rule if {
	some rc in input.resource_changes
	rc.type == "aws_vpc_security_group_ingress_rule"
	after := rc.change.after
	rule := {
		"address": rc.address,
		"cidrs": [c | some c in [after.cidr_ipv4, after.cidr_ipv6]; c != null],
		"unknown": any_unknown(rc, ["cidr_ipv4", "cidr_ipv6"]),
		"proto": after.ip_protocol,
		"from": after.from_port,
		"to": after.to_port,
	}
}

# --- Decisions ---

deny contains msg if {
	some rule in rules
	some cidr in rule.cidrs
	cidr in world
	allows_ssh(rule.proto, rule.from, rule.to)
	msg := sprintf("%s: SSH (TCP 22) open to %s. Allow a specific CIDR instead", [rule.address, cidr])
}

warn contains msg if {
	some rule in rules
	rule.unknown
	allows_ssh(rule.proto, rule.from, rule.to)
	msg := sprintf("%s: a CIDR on a rule that allows SSH is only known after apply, so it can't be checked", [rule.address])
}

# --- Helpers ---

nulls_to_empty(x) := [] if x == null

nulls_to_empty(x) := x if x != null

# after_unknown marks a whole attribute (true) or single list elements ([false, true]) as unknown.
unknown_value(v) if v == true

unknown_value(v) if true in v

any_unknown(rc, attrs) if {
	some attr in attrs
	unknown_value(object.get(rc.change, ["after_unknown", attr], false))
}

default any_unknown(_, _) := false

inline_cidrs_unknown(rc, i) if {
	unknown_value(object.get(rc.change, ["after_unknown", "ingress"], false))
}

inline_cidrs_unknown(rc, i) if {
	some attr in ["cidr_blocks", "ipv6_cidr_blocks"]
	unknown_value(object.get(rc.change, ["after_unknown", "ingress", i, attr], false))
}

default inline_cidrs_unknown(_, _) := false
