"""Synthetic telemetry for a content-processing platform (demo + tests)."""
import random

BASE = {  # metric: (mean, stddev)
    "ingestion_rate": (120.0, 6.0),     # documents / minute
    "ocr_failure_rate": (1.5, 0.3),     # percent
    "queue_latency_s": (8.0, 1.0),      # seconds
    "indexing_lag_s": (20.0, 3.0),      # seconds
}

SCENARIOS = {
    "ocr_degradation": {"ocr_failure_rate": 14.0},
    "ingestion_surge": {"ingestion_rate": 420.0, "queue_latency_s": 55.0, "indexing_lag_s": 90.0},
    "stuck_queue": {"queue_latency_s": 90.0},
    "upstream_outage": {"ingestion_rate": 5.0},
}


def generate(tenant: str, start: float, n: int, seed: int = 0, step: float = 60.0,
             scenario: str | None = None) -> list[dict]:
    """n points per metric; if `scenario` is set, the LAST point carries the incident."""
    rng, out = random.Random(seed), []
    for i in range(n):
        for name, (mu, sd) in BASE.items():
            v = max(0.0, rng.gauss(mu, sd))
            if scenario and i == n - 1 and name in SCENARIOS[scenario]:
                v = SCENARIOS[scenario][name]
            out.append({"tenant": tenant, "name": name, "ts": start + i * step, "value": round(v, 3)})
    return out
