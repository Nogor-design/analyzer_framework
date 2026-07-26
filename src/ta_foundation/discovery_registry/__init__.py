"""Probe identity + registry layer for cross-probe Bonferroni and graveyard refusal.

Public surface:
- compute_probe_identity(cfg_raw) -> ProbeIdentity
- probe_hash(identity) -> str
- ProbeRegistry / GraveyardRegistry — JSON-backed under output/
- check_graveyard(identity, registry) -> RegistryHit | None
"""

from .hashing import (
    ProbeIdentity,
    SignalSpec,
    compute_probe_identity,
    probe_hash,
    jaccard_overlap,
)
from .registry import (
    ProbeRegistry,
    GraveyardRegistry,
    ProbeRecord,
    GraveyardRecord,
    RegistryHit,
    check_graveyard,
)

__all__ = [
    "ProbeIdentity",
    "SignalSpec",
    "compute_probe_identity",
    "probe_hash",
    "jaccard_overlap",
    "ProbeRegistry",
    "GraveyardRegistry",
    "ProbeRecord",
    "GraveyardRecord",
    "RegistryHit",
    "check_graveyard",
]
