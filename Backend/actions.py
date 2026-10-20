"""Stage 5: guarded action execution.

The AI can only *propose* actions from this registry. Every action declares a
risk level and a strict parameter schema; unknown actions/params are rejected.
Implementations here are simulated - replace the bodies with real calls
(Kubernetes, Documentum/xECM admin APIs, queue brokers, PagerDuty...).
"""
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class ActionSpec:
    name: str
    risk: str                       # low | medium | high
    description: str
    params: dict[str, type]
    fn: Callable[[dict], str]


def _restart_worker(p): return f"Restarted worker pool '{p['service']}' (rolling restart)."
def _scale_workers(p): return f"Scaled '{p['service']}' by {p['delta']:+d} workers."
def _pause_ingestion(p): return f"Paused ingestion for tenant '{p['tenant']}' for {p['minutes']} min."
def _reindex_tenant(p): return f"Queued full reindex for tenant '{p['tenant']}'."
def _notify_oncall(p): return f"Paged on-call: {p['message']}"


REGISTRY: dict[str, ActionSpec] = {s.name: s for s in [
    ActionSpec("restart_worker", "medium", "Rolling restart of a worker pool",
               {"service": str}, _restart_worker),
    ActionSpec("scale_workers", "low", "Add/remove workers in a pool",
               {"service": str, "delta": int}, _scale_workers),
    ActionSpec("pause_ingestion", "high", "Temporarily pause intake for a tenant",
               {"tenant": str, "minutes": int}, _pause_ingestion),
    ActionSpec("reindex_tenant", "high", "Full search reindex for a tenant",
               {"tenant": str}, _reindex_tenant),
    ActionSpec("notify_oncall", "low", "Page a human with context",
               {"message": str}, _notify_oncall),
]}


def validate(action: str, params: dict) -> None:
    spec = REGISTRY.get(action)
    if spec is None:
        raise ValueError(f"Unknown action '{action}'. Allowed: {sorted(REGISTRY)}")
    if set(params) != set(spec.params):
        raise ValueError(f"'{action}' requires params {sorted(spec.params)}, got {sorted(params)}")
    for k, t in spec.params.items():
        if not isinstance(params[k], t) or isinstance(params[k], bool):
            raise ValueError(f"param '{k}' of '{action}' must be {t.__name__}")


def execute(action: str, params: dict) -> dict:
    validate(action, params)
    try:
        return {"ok": True, "message": REGISTRY[action].fn(params)}
    except Exception as exc:  # real integrations will fail; surface it, never crash the loop
        return {"ok": False, "message": f"{type(exc).__name__}: {exc}"}


def catalogue() -> list[dict]:
    return [{"name": s.name, "risk": s.risk, "description": s.description,
             "params": {k: t.__name__ for k, t in s.params.items()}} for s in REGISTRY.values()]
