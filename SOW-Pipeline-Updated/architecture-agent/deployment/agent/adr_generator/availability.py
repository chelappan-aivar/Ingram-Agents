"""The deployment availability model: how many Availability Zones a design spans.

This used to be a hardcoded single-AZ rule spread across prompts, the usage registry and the
usage rules. A single-AZ design fails the Well-Architected Reliability pillar (REL10-BP02
expects a workload to span at least two AZs), and because `usage.json` feeds the cost
calculator, the AZ count also changes the price. Both reasons mean it belongs in one place.

The policy is set by `AVAILABILITY_ZONES` (default 2) and flows three ways:

  - into the ADR prompt, so the model designs for the right number of zones
  - into the usage prompt and the usage rules, so `multi_az` / `availability_zones` in
    usage.json match the design the diagram is drawn from
  - into the rendered reports, so the stated deployment matches what was priced

`from_design` reads the count back off a finished design, so the usage estimate follows the
design that was actually produced rather than the configured default.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # avoid a circular import at runtime (contracts imports nothing from here)
    from adr_generator.contracts import Design

DEFAULT_ZONE_COUNT = 2          # Well-Architected Reliability: at least two AZs
MIN_ZONE_COUNT = 1              # 1 is allowed, but it is a documented exception, not the default
MAX_ZONE_COUNT = 6              # no commercial Region exposes more than six AZs
ZONE_LETTERS = "abcdef"


def clamp_zone_count(value: Any) -> int:
    """A usable zone count from anything the caller supplies. Never raises."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return DEFAULT_ZONE_COUNT
    return max(MIN_ZONE_COUNT, min(MAX_ZONE_COUNT, n))


@dataclass(frozen=True)
class AvailabilityPolicy:
    """How many AZs the workload spans, and the wording that follows from it."""

    zone_count: int = DEFAULT_ZONE_COUNT

    def __post_init__(self) -> None:
        object.__setattr__(self, "zone_count", clamp_zone_count(self.zone_count))

    # ---------------------------------------------------------------- facts
    @property
    def multi_az(self) -> bool:
        return self.zone_count > 1

    def zone_names(self, region: str) -> list[str]:
        """Conventional zone names for a Region, e.g. us-east-1 -> ['us-east-1a', 'us-east-1b']."""
        region = (region or "").strip()
        if not region:
            return []
        return [f"{region}{ZONE_LETTERS[i]}" for i in range(self.zone_count)]

    def usage_fields(self) -> dict[str, Any]:
        """The usage.json fields this policy decides, whatever the model answered."""
        return {"multi_az": self.multi_az, "availability_zones": self.zone_count}

    # ---------------------------------------------------------------- wording
    def phrase(self) -> str:
        """Short human phrase, e.g. 'two Availability Zones'."""
        words = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}
        zone = "Availability Zone" if self.zone_count == 1 else "Availability Zones"
        return f"{words[self.zone_count]} {zone}"

    def deployment_summary(self, region: str, zones: list[str] | None = None) -> str:
        """One line for a report table, e.g. 'One Region (us-east-1), two Availability Zones (us-east-1a, us-east-1b)'.

        `zones=None` means "derive conventional names"; an empty list means the design named
        none, which is reported honestly rather than filled in with invented zone names.
        """
        names = [z for z in (self.zone_names(region) if zones is None else zones) if z]
        shown = ", ".join(names) if names else "not specified"
        return f"One Region ({region or 'Not specified'}), {self.phrase()} ({shown})"

    def design_rule(self) -> str:
        """The availability rule appended to the ADR system prompt."""
        if not self.multi_az:
            return (
                "## Availability model (authoritative)\n"
                "- **One Region and one Availability Zone.** Use the Region the SRS states (default us-east-1) "
                "and name exactly one AZ in `availability_zones` (default `<region>a`). Never use Multi-AZ "
                "deployment options, standby instances, cross-AZ replicas, workloads in a second AZ or a DR "
                "Region. Recovery comes from backups and snapshots.\n"
                "- This single-AZ scope is a deliberate exception to the Well-Architected Reliability pillar. "
                "For every component whose availability depends on it, record the residual risk and the "
                "backup-based recovery path in the decision's consequences, and raise an open question about "
                "expanding to two AZs after the pilot.\n"
                "- Do not describe the solution as highly available or as following Well-Architected "
                "Reliability best practice."
            )
        return (
            "## Availability model (authoritative)\n"
            f"- **One Region, {self.phrase()}.** Use the Region the SRS states (default us-east-1) and name "
            f"exactly {self.zone_count} AZs in `availability_zones` (default `<region>a`, `<region>b`, ... in "
            "order).\n"
            f"- Spread every component that is not Region-level or global across the {self.zone_count} zones, "
            "and say so in its configuration. Subnets come in one public and one private per zone, each its own "
            "boundary with the AZ boundary as `parent`.\n"
            "- Managed services that offer it run with their multi-AZ option on: Amazon RDS and Amazon "
            "ElastiCache use a Multi-AZ deployment with a standby in another zone, Application Load Balancers "
            f"and NAT Gateways exist in each of the {self.zone_count} zones, and Auto Scaling groups and ECS or "
            "EKS services spread their tasks across all of them.\n"
            "- State the multi-AZ choice in the `sku_summary` of every component it applies to (for example "
            "\"db.t4g.medium, PostgreSQL 17, gp3 50 GiB, Multi-AZ\"), because the pricing analyst reads that "
            "line.\n"
            f"- Cross-AZ data transfer and the per-zone NAT Gateways are real costs. Do not hide them: a "
            f"{self.phrase()} deployment is more expensive than one zone, and that is the intended trade-off "
            "for resiliency.\n"
            "- Never place the whole workload in a single AZ, and never add a second Region or a DR Region "
            "unless the SRS asks for one."
        )

    def usage_rule(self) -> str:
        """The availability rule appended to the usage-estimator system prompt."""
        if not self.multi_az:
            return (
                "## Availability model (authoritative)\n"
                "- **One Region and one Availability Zone.** Never add standby instances, cross-AZ replicas or "
                "a second Region. The `multi_az` and `availability_zones` fields are set by code and are not "
                "yours to fill."
            )
        return (
            "## Availability model (authoritative)\n"
            f"- **One Region, {self.phrase()}.** The `multi_az` and `availability_zones` fields are set by code "
            "from the design and are not yours to fill, but every figure you do fill must match a "
            f"{self.phrase()} deployment.\n"
            f"- Count the resources that exist once per zone once per zone: {self.zone_count} NAT Gateways, "
            f"{self.zone_count} subnets' worth of endpoints, and database instance hours that include the "
            "standby (a Multi-AZ Amazon RDS instance bills roughly twice the single-instance hours).\n"
            "- Include cross-AZ data transfer wherever a flow crosses zones, for example between a load "
            "balancer and targets in another zone, or between an application and a database standby.\n"
            "- Do not reduce per-zone counts to save money. The deployment is the design's; your job is to "
            "report its usage accurately."
        )

    def report_method_line(self) -> str:
        """The method bullet in usage_assumptions.md."""
        phrase = self.phrase()
        return (
            f"- {phrase[0].upper()}{phrase[1:]}: multi_az is {str(self.multi_az).lower()} and "
            f"availability_zones is {self.zone_count}, set by code from the design. memory_gb is computed "
            "from memory_mb."
        )

    # ---------------------------------------------------------------- construction
    @classmethod
    def from_zone_count(cls, value: Any) -> "AvailabilityPolicy":
        return cls(clamp_zone_count(value))

    @classmethod
    def from_settings(cls, settings: Any) -> "AvailabilityPolicy":
        return cls(clamp_zone_count(getattr(settings, "availability_zones", DEFAULT_ZONE_COUNT)))

    @classmethod
    def from_design(cls, design: "Design") -> "AvailabilityPolicy":
        """Read the model back off a finished design, so usage follows the design, not the default.

        The AZ boundaries are the authority (they are what the diagram draws); `availability_zones`
        is the fallback when a design is fully serverless and declares no boundaries.
        """
        az_boundaries = [b for b in design.boundaries if b.type == "az"]
        count = len(az_boundaries) or len(design.availability_zones)
        return cls(clamp_zone_count(count or DEFAULT_ZONE_COUNT))
