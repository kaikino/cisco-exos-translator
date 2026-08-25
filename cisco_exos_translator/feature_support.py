# Deterministic support classification for Cisco commands the translator does
# not emit EXOS output for.
#
# This is the extension point for feature coverage: adding a feature means
# adding a rule here (or calling register_feature from another module), not
# scattering special cases through the parser or generator. Rules are matched
# in order, first match wins; anything unmatched is reported as unsupported
# with a generic reason, so nothing is silently classified as fine.

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .findings import TranslationStatus


@dataclass(frozen=True)
class FeatureRule:
    feature: str  # grouping key used in findings, e.g. "spanning-tree"
    pattern: "re.Pattern"
    status: str
    reason: str
    action: Optional[str] = None
    # True when the matching line may carry a credential. The line text is then
    # never copied into a finding -- only the command keyword.
    redact: bool = False


def _rule(feature, regex, reason, status=TranslationStatus.UNSUPPORTED,
          action=None, redact=False) -> FeatureRule:
    return FeatureRule(
        feature=feature,
        pattern=re.compile(regex, re.IGNORECASE),
        status=status,
        reason=reason,
        action=action,
        redact=redact,
    )


# Ordered; credential-bearing rules come first so they cannot be shadowed.
FEATURE_RULES: list = [
    _rule("credentials", r"^(username|enable\s+(secret|password)|password\b)",
          "local credentials are not carried over to EXOS",
          action="create EXOS accounts separately; do not reuse the Cisco hashes",
          redact=True),
    _rule("credentials", r"^(radius|tacacs)([-\s]server)?\b.*\bkey\b",
          "AAA server shared secrets are not carried over to EXOS",
          action="re-enter the shared secret on the EXOS switch", redact=True),
    _rule("snmp", r"^snmp-server\s+(community|user|host)\b",
          "SNMP credentials/targets are not translated",
          action="configure SNMP on EXOS separately", redact=True),
    _rule("snmp", r"^snmp-server\b", "SNMP settings are outside L2 scope",
          action="configure SNMP on EXOS separately"),

    _rule("aaa", r"^(aaa|radius|tacacs)\b", "AAA / RADIUS / TACACS+ is not translated",
          action="configure EXOS AAA (radius/tacacs) separately"),
    _rule("dot1x", r"^(dot1x|mab|authentication\s|access-session)\b",
          "802.1X / MAB port authentication is not translated",
          action="configure EXOS netlogin separately"),

    _rule("spanning-tree", r"^spanning-tree\b",
          "spanning tree is not translated; EXOS defaults differ from Cisco PVST+",
          action="choose and configure an EXOS STP mode (STP/RSTP/MSTP) explicitly"),
    _rule("vtp", r"^vtp\b", "VTP has no EXOS equivalent",
          action="VLANs are created explicitly in the .xsf; no VLAN distribution protocol is configured"),
    _rule("discovery", r"^(cdp|lldp)\b", "discovery protocol settings are not translated",
          action="EXOS enables LLDP separately; CDP is not supported"),

    _rule("port-security", r"^switchport\s+port-security\b",
          "port security is not translated",
          action="review EXOS MAC limiting / lock-learning for the same ports"),
    _rule("storm-control", r"^storm-control\b", "storm control is not translated",
          action="review EXOS rate-limit / flood-control for the same ports"),
    _rule("qos", r"^(mls\s+qos|auto\s+qos|service-policy|priority-queue|srr-queue|"
                 r"class-map|policy-map|qos\b)",
          "QoS classification/policing is not translated",
          action="rebuild the QoS policy on EXOS; the .xsf contains none"),

    _rule("voice-vlan", r"^switchport\s+voice\s+vlan\b",
          "voice VLAN is not translated as a distinct concept",
          status=TranslationStatus.UNSUPPORTED,
          action="add the voice VLAN tagged to the port on EXOS if phones need it"),
    _rule("port-settings", r"^(speed|duplex|mtu|load-interval|power\s+inline|"
                           r"switchport\s+nonegotiate|switchport\s+trunk\s+encapsulation)\b",
          "physical/port-tuning settings are outside L2 VLAN scope",
          action="set the equivalent EXOS port settings if the defaults do not match"),

    _rule("l3-routing", r"^(router\s+\S+|ip\s+routing\s+\S+|ipv6\b|vrf\b)",
          "dynamic routing / VRF is outside the translator's L3 scope (SVI addresses "
          "and static routes only)",
          action="rebuild routing configuration on EXOS"),
    _rule("dhcp-relay", r"^ip\s+helper-address\b", "DHCP relay is not translated",
          action="configure EXOS BOOTP relay for the equivalent VLANs"),
    _rule("first-hop-redundancy", r"^(standby|vrrp|glbp)\b",
          "HSRP/VRRP/GLBP is not translated",
          action="configure EXOS VRRP if gateway redundancy is required"),
    _rule("acl", r"^(permit|deny|remark|access-list\s+\d+)\b",
          "the ACL entry uses a construct outside the supported IPv4 subset "
          "(see the finding's parser_reasons)",
          status=TranslationStatus.PARTIALLY_TRANSLATED,
          action="add the equivalent entry to the generated .pol file by hand; a "
                 "dropped deny over-permits traffic"),
    _rule("acl", r"^ip\s+access-group\s+\S+\s+out\b",
          "egress ACLs are not translated; only 'in' is supported",
          status=TranslationStatus.UNSUPPORTED,
          action="review whether the egress filter is still required on EXOS"),

    _rule("management", r"^(line\s|banner\b|ntp\b|clock\b|logging\b|ip\s+domain|"
                        r"ip\s+ssh|crypto\b|ip\s+http|service\b)",
          "management-plane settings are outside L2 scope",
          action="configure management access, logging and time on EXOS separately"),
    _rule("platform", r"^(version|boot\b|archive\b|license\b|system\s+mtu|"
                      r"vlan\s+internal|end$|exit$|do\s)",
          "platform/housekeeping command with no EXOS output",
          status=TranslationStatus.UNSUPPORTED,
          action=None),
]


def register_feature(rule: FeatureRule, *, first: bool = False) -> None:
    # Extension point for new feature coverage.
    if first:
        FEATURE_RULES.insert(0, rule)
    else:
        FEATURE_RULES.append(rule)


_DEFAULT = FeatureRule(
    feature="unclassified",
    pattern=re.compile(r".*"),
    status=TranslationStatus.UNSUPPORTED,
    reason="no EXOS translation is implemented for this command",
    action="review the source line and port it manually if it matters",
)


def classify(text: str) -> FeatureRule:
    # Classify one untranslated Cisco config line.
    stripped = text.strip()
    for rule in FEATURE_RULES:
        if rule.pattern.match(stripped):
            return rule
    return _DEFAULT


# First token(s) of a command, used as a redaction-safe label.
_KEYWORD = re.compile(r"^([A-Za-z0-9-]+(?:\s+[A-Za-z0-9-]+)?)")


def safe_label(text: str, rule: FeatureRule) -> str:
    # The text to show for a line: verbatim, unless the rule may carry a secret.
    stripped = text.strip()
    if not rule.redact:
        return stripped
    match = _KEYWORD.match(stripped)
    return f"{match.group(1)} <redacted>" if match else "<redacted>"
