"""Stage 2: anomaly detection.

Uses a robust z-score (median / MAD) over a rolling baseline, which is far less
distorted by past spikes than mean/stddev. Thresholds are per (tenant, metric)
and are tuned by human feedback (see pipeline.reject / feedback loop).
Simultaneous anomalies for a tenant are grouped into ONE incident so the
investigator sees the full picture (e.g. surge + latency + lag together).
"""
import json
import statistics

ACTIVE = ("open", "pending_approval", "executed")


def robust_z(window: list[float], x: float) -> tuple[float, float]:
    med = statistics.median(window)
    mad = statistics.median(abs(v - med) for v in window)
    scale = 1.4826 * mad
    if scale < 1e-9:                      # flat baseline -> small relative floor
        scale = max(abs(med) * 0.05, 1e-6)
    return (x - med) / scale, med


def severity(z: float) -> str:
    a = abs(z)
    return "critical" if a >= 20 else "high" if a >= 10 else "medium"


class Detector:
    def __init__(self, db, window: int = 30, default_z: float = 6.0, min_points: int = 10):
        self.db, self.window, self.default_z, self.min_points = db, window, default_z, min_points

    def threshold(self, tenant: str, metric: str) -> float:
        row = self.db.one("SELECT z FROM thresholds WHERE tenant=? AND metric=?", (tenant, metric))
        return row["z"] if row else self.default_z

    def run(self) -> list[int]:
        created = []
        tenants = [r["tenant"] for r in self.db.query("SELECT DISTINCT tenant FROM metrics")]
        for tenant in tenants:
            marks = ",".join("?" * len(ACTIVE))
            if self.db.one(
                f"SELECT id FROM anomalies WHERE tenant=? AND status IN ({marks})",
                (tenant, *ACTIVE),
            ):
                continue  # one active incident per tenant
            flagged = self._scan_tenant(tenant)
            if not flagged:
                continue
            flagged.sort(key=lambda f: abs(f["zscore"]), reverse=True)
            primary, related = flagged[0], flagged[1:]
            aid = self.db.execute(
                "INSERT INTO anomalies(tenant,metric,ts,value,baseline,zscore,severity,related) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (tenant, primary["metric"], primary["ts"], primary["value"], primary["baseline"],
                 primary["zscore"], severity(primary["zscore"]), json.dumps(related)),
            )
            created.append(aid)
        return created

    def _scan_tenant(self, tenant: str) -> list[dict]:
        out = []
        names = [r["name"] for r in self.db.query(
            "SELECT DISTINCT name FROM metrics WHERE tenant=?", (tenant,))]
        for m in names:
            rows = self.db.query(
                "SELECT ts,value FROM metrics WHERE tenant=? AND name=? ORDER BY ts DESC LIMIT ?",
                (tenant, m, self.window + 1),
            )
            if len(rows) < self.min_points + 1:
                continue
            latest, history = rows[0], [r["value"] for r in rows[1:]]
            z, base = robust_z(history, latest["value"])
            if abs(z) >= self.threshold(tenant, m):
                out.append({"metric": m, "ts": latest["ts"], "value": latest["value"],
                            "baseline": base, "zscore": z})
        return out
