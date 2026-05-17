"""CLI-side helpers for graveyard refusal: YAML resolver and post-run hook."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml

from .hashing import KNOWN_DISCOVERY_BLOCKS, ProbeIdentity, compute_probe_identity
from .registry import ProbeRegistry

DEFAULT_CUMULATIVE_DECAY_FACTOR = 10


def build_yaml_resolver(cache: Optional[Dict[str, ProbeIdentity]] = None) -> Callable[[str], Optional[ProbeIdentity]]:
    """Returns a resolver(yaml_path) -> ProbeIdentity that re-reads from disk.

    Memoised through `cache` (defaults to a fresh dict) so the near-match
    walk only parses each referenced graveyard YAML once per CLI invocation.
    Missing files or YAML errors yield None — the check_graveyard caller
    treats that as 'cannot compare' and skips the entry.
    """
    if cache is None:
        cache = {}

    def resolver(yaml_path: str) -> Optional[ProbeIdentity]:
        if yaml_path in cache:
            return cache[yaml_path]
        p = Path(yaml_path)
        if not p.is_file():
            cache[yaml_path] = None  # type: ignore[assignment]
            return None
        try:
            with p.open("r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
        except (yaml.YAMLError, OSError):
            cache[yaml_path] = None  # type: ignore[assignment]
            return None
        ident = compute_probe_identity(raw)
        cache[yaml_path] = ident
        return ident

    return resolver


def compute_effective_n_hypotheses(
    output_dir: Path,
    identity: ProbeIdentity,
    *,
    decay_factor: int = DEFAULT_CUMULATIVE_DECAY_FACTOR,
) -> Dict[str, Any]:
    """Compute the project-cumulative effective Bonferroni denominator.

    Returns {"cumulative_family": int, "cumulative_global": int,
             "decay_factor": int, "families": [str]}.

    The CLI then applies `effective = max(yaml_n, cumulative_family // decay_factor)`
    per hardening block, preferring family-filtered cumulative (cross-family
    hypotheses are roughly independent).
    """
    registry = ProbeRegistry(output_dir)
    families = [f"{s.block}::{s.signal}" for s in identity.signals]
    cumulative_family = registry.cumulative_hypotheses(family_filter=families) if families else 0
    cumulative_global = registry.cumulative_hypotheses()
    return {
        "cumulative_family": cumulative_family,
        "cumulative_global": cumulative_global,
        "decay_factor": decay_factor,
        "families": families,
    }


def inject_effective_penalty(
    cfg_raw: Dict[str, Any],
    effective_info: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Mutate each enabled discovery block's `hardening.n_hypotheses_tested` to
    `max(yaml_value, cumulative_family // decay_factor)`. Records the YAML
    original under `hardening.yaml_n_hypotheses_tested` for audit.

    Returns a list of per-block mutation records the CLI can log.
    """
    cumulative_family = int(effective_info.get("cumulative_family") or 0)
    decay_factor = max(int(effective_info.get("decay_factor") or DEFAULT_CUMULATIVE_DECAY_FACTOR), 1)
    floor = cumulative_family // decay_factor
    mutations: List[Dict[str, Any]] = []

    for block_name in KNOWN_DISCOVERY_BLOCKS:
        block = cfg_raw.get(block_name)
        if not isinstance(block, dict) or not block.get("enabled", False):
            continue
        hardening = block.get("hardening")
        if not isinstance(hardening, dict) or not hardening.get("enabled", False):
            continue
        yaml_n = int(hardening.get("n_hypotheses_tested") or 1)
        effective_n = max(yaml_n, floor)
        if effective_n == yaml_n:
            continue
        # Preserve YAML original for audit; promote effective.
        hardening["yaml_n_hypotheses_tested"] = yaml_n
        hardening["n_hypotheses_tested"] = effective_n
        hardening["cumulative_family"] = cumulative_family
        hardening["cumulative_decay_factor"] = decay_factor
        mutations.append({
            "block": block_name,
            "yaml_n": yaml_n,
            "effective_n": effective_n,
            "cumulative_family": cumulative_family,
            "decay_factor": decay_factor,
        })
    return mutations


def post_run_register(output_dir: Path, discovery_dirs: Optional[list] = None) -> None:
    """Append any newly-written sidecars to the probe + graveyard registries.

    Wraps `backfill()`. Idempotent — entries already present are skipped.
    Quiet by default (no console output) so the CLI's normal completion
    log isn't cluttered. Failures are swallowed: registry hygiene should
    never block a successful discovery run.
    """
    try:
        from .backfill import backfill
        backfill(output_dir, discovery_dirs=discovery_dirs, verbose=False)
    except Exception as e:  # pragma: no cover - best-effort hook
        print(f"[ta_foundation] WARNING: registry update failed: {type(e).__name__}: {e}")
