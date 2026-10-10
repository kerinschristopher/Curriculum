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
# A value known only after apply (e.g. taken from another resource) can't be checked: that is a
# warn, not a deny, so the plan still passes but the reader is told to look. It covers an unknown
# CIDR, an unknown protocol or port range, and a whole inline ingress list (or one of its rules)
# that is unknown, e.g. a dynamic "ingress" block over another resource's output.
#
# The plan JSON leaves an unknown attribute out of `after` (an unknown list element becomes
# null) and marks it true in `after_unknown`. So every read of `after` below uses object.get with
# a null default: a missing attribute must not silently drop the rule.

package main

import rego.v1

world := {"0.0.0.0/0", "::/0"}

all_protocols := {"-1", "all"}

tcp := {"tcp", "6"}

# The plan keeps the protocol exactly as written, so "TCP" must match "tcp". sprintf makes a
# null (unknown) protocol a harmless string instead of an error.
norm_proto(p) := lower(sprintf("%v", [p]))

# Does a rule with this protocol and port range let SSH through?
allows_ssh(proto, _, _) if norm_proto(proto) in all_protocols

allows_ssh(proto, from, to) if {
	norm_proto(proto) in tcp
	is_number(from) # an unknown port is null, and Rego sorts null before every number
	is_number(to)
	from <= 22
	to >= 22
}

# --- One entry per ingress rule, whatever its shape ---
# {address, cidrs, unknown, proto, from, to, proto_unknown, ports_unknown}; unknown = a CIDR is unknown.

rules contains rule if {
	some rc in input.resource_changes
	rc.type == "aws_security_group"
	is_object(rc.change.after)
	some i, ing in object.get(rc.change.after, "ingress", [])
	is_object(ing) # an unknown rule is null here; see inline_ingress_unknown
	rule := {
		"address": sprintf("%s ingress[%d]", [rc.address, i]),
		"cidrs": array.concat(nulls_to_empty(object.get(ing, "cidr_blocks", null)), nulls_to_empty(object.get(ing, "ipv6_cidr_blocks", null))),
		"unknown": inline_unknown(rc, i, ["cidr_blocks", "ipv6_cidr_blocks"]),
		"proto": object.get(ing, "protocol", null),
		"from": object.get(ing, "from_port", null),
		"to": object.get(ing, "to_port", null),
		"proto_unknown": inline_unknown(rc, i, ["protocol"]),
		"ports_unknown": inline_unknown(rc, i, ["from_port", "to_port"]),
	}
}

rules contains rule if {
	some rc in input.resource_changes
	rc.type == "aws_security_group_rule"
	after := rc.change.after
	after.type == "ingress"
	rule := {
		"address": rc.address,
		"cidrs": array.concat(nulls_to_empty(object.get(after, "cidr_blocks", null)), nulls_to_empty(object.get(after, "ipv6_cidr_blocks", null))),
		"unknown": any_unknown(rc, ["cidr_blocks", "ipv6_cidr_blocks"]),
		"proto": object.get(after, "protocol", null),
		"from": object.get(after, "from_port", null),
		"to": object.get(after, "to_port", null),
		"proto_unknown": any_unknown(rc, ["protocol"]),
		"ports_unknown": any_unknown(rc, ["from_port", "to_port"]),
	}
}

rules contains rule if {
	some rc in input.resource_changes
	rc.type == "aws_vpc_security_group_ingress_rule"
	after := rc.change.after
	is_object(after)
	rule := {
		"address": rc.address,
		"cidrs": [c | some c in [object.get(after, "cidr_ipv4", null), object.get(after, "cidr_ipv6", null)]; c != null],
		"unknown": any_unknown(rc, ["cidr_ipv4", "cidr_ipv6"]),
		"proto": object.get(after, "ip_protocol", null),
		"from": object.get(after, "from_port", null),
		"to": object.get(after, "to_port", null),
		"proto_unknown": any_unknown(rc, ["ip_protocol"]),
		"ports_unknown": any_unknown(rc, ["from_port", "to_port"]),
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

# The CIDR is unknown, on a rule that allows (or, with unknown ports, may allow) SSH.
warn contains msg if {
	some rule in rules
	rule.unknown
	may_allow_ssh(rule)
	msg := sprintf("%s: a CIDR on a rule that may allow SSH is only known after apply, so it can't be checked", [rule.address])
}

# The CIDR is the whole internet, but the protocol or ports are unknown.
warn contains msg if {
	some rule in rules
	some cidr in rule.cidrs
	cidr in world
	not allows_ssh(rule.proto, rule.from, rule.to) # otherwise it's already a deny
	may_allow_ssh(rule)
	msg := sprintf("%s: open to %s, but its protocol or port range is only known after apply, so it can't be checked for SSH", [rule.address, cidr])
}

# The inline ingress list, or one of its rules, is unknown: there is nothing to check yet.
warn contains msg if {
	some rc in input.resource_changes
	rc.type == "aws_security_group"
	inline_ingress_unknown(rc)
	msg := sprintf("%s: its ingress rules are only known after apply, so they can't be checked for SSH", [rc.address])
}

# --- Helpers ---

may_allow_ssh(rule) if allows_ssh(rule.proto, rule.from, rule.to)

may_allow_ssh(rule) if rule.proto_unknown

may_allow_ssh(rule) if {
	rule.ports_unknown
	norm_proto(rule.proto) in tcp
}

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

inline_unknown(rc, i, attrs) if {
	some attr in attrs
	unknown_value(object.get(rc.change, ["after_unknown", "ingress", i, attr], false))
}

default inline_unknown(_, _, _) := false

# true: the whole list. [false, true]: one rule (which `after` holds as null).
inline_ingress_unknown(rc) if unknown_value(object.get(rc.change, ["after_unknown", "ingress"], false))
