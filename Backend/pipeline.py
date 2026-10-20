"""Orchestrator: the full closed loop.

collect -> detect -> investigate -> recommend -> HUMAN APPROVAL -> execute -> verify -> learn

Recommendation state machine:
    pending_approval -> executed -> resolved | unresolved      (approved path)
    pending_approval -> rejected                                (human said no)
    pending_approval -> failed                                  (action errored)
Every transition is written to the audit table.
"""
import json
import time

from . import actions
from .detector import Detector, robust_z
from .investigator import RuleBasedInvestigator


class OpsPilot:
    def __init__(self, db, investigator=None, window: int = 30, z_threshold: float = 6.0):
        self.db = db
        self.window = window
        self.detector = Detector(db, window=window, default_z=z_threshold)
        self.investigator = investigator or RuleBasedInvestigator()

    # ---- helpers -------------------------------------------------------
    def audit(self, actor: str, event: str, ref: str, detail: dict | str = "") -> None:
        self.db.execute("INSERT INTO audit(ts,actor,event,ref,detail) VALUES(?,?,?,?,?)",
                        (time.time(), actor, event, ref,
                         detail if isinstance(detail, str) else json.dumps(detail)))

    def _rec(self, rec_id: int, expect: str) -> dict:
        rec = self.db.one("SELECT * FROM recommendations WHERE id=?", (rec_id,))
        if rec is None:
            raise KeyError(f"recommendation {rec_id} not found")
        if rec["status"] != expect:
            raise ValueError(f"recommendation {rec_id} is '{rec['status']}', expected '{expect}'")
        return rec

    # ---- 1. collect ----------------------------------------------------
    def ingest(self, metrics: list[dict]) -> int:
        self.db.executemany(
            "INSERT INTO metrics(tenant,name,ts,value) VALUES(?,?,?,?)",
            [(m["tenant"], m["name"], m["ts"], m["value"]) for m in metrics])
        return len(metrics)

    # ---- 2. detect -----------------------------------------------------
    def detect(self) -> list[int]:
        ids = self.detector.run()
        for i in ids:
            self.audit("system", "anomaly_detected", f"anomaly:{i}")
        return ids

    # ---- 3+4. investigate & recommend ---------------------------------
    def _history(self, metric: str) -> dict:
        rows = self.db.query(
            "SELECT action, COUNT(*) n, AVG(resolved) rate FROM feedback "
            "WHERE kind='outcome' AND metric=? GROUP BY action", (metric,))
        return {r["action"]: {"n": r["n"], "success_rate": round(r["rate"], 2)} for r in rows}

    def investigate(self, anomaly_id: int) -> int:
        a = self.db.one("SELECT * FROM anomalies WHERE id=?", (anomaly_id,))
        if a is None:
            raise KeyError(f"anomaly {anomaly_id} not found")
        if a["status"] != "open":
            raise ValueError(f"anomaly {anomaly_id} is '{a['status']}', expected 'open'")
        recent = [r["value"] for r in self.db.query(
            "SELECT value FROM metrics WHERE tenant=? AND name=? ORDER BY ts DESC LIMIT 10",
            (a["tenant"], a["metric"]))][::-1]
        ctx = {
            "tenant": a["tenant"],
            "primary": {k: a[k] for k in ("metric", "value", "baseline", "zscore", "severity")},
            "related": json.loads(a["related"] or "[]"),
            "recent_values": recent,
            "history": self._history(a["metric"]),
        }
        f = self.investigator.investigate(ctx)
        actions.validate(f.action, f.params)
        now = time.time()
        self.db.execute(
            "INSERT INTO investigations(anomaly_id,root_cause,confidence,hypotheses,evidence,source,created) "
            "VALUES(?,?,?,?,?,?,?)",
            (anomaly_id, f.root_cause, f.confidence, json.dumps(f.hypotheses),
             json.dumps(f.evidence), f.source, now))
        rec_id = self.db.execute(
            "INSERT INTO recommendations(anomaly_id,action,params,rationale,risk,status,created) "
            "VALUES(?,?,?,?,?,?,?)",
            (anomaly_id, f.action, json.dumps(f.params), f.rationale,
             actions.REGISTRY[f.action].risk, "pending_approval", now))
        self.db.execute("UPDATE anomalies SET status='pending_approval' WHERE id=?", (anomaly_id,))
        self.audit(f.source, "recommendation_created", f"rec:{rec_id}",
                   {"action": f.action, "params": f.params, "confidence": f.confidence})
        return rec_id

    # ---- 5. human approval -> execute ---------------------------------
    def approve(self, rec_id: int, approver: str, note: str = "") -> dict:
        rec = self._rec(rec_id, "pending_approval")
        params = json.loads(rec["params"])
        self.audit(approver, "approved", f"rec:{rec_id}", note)
        result = actions.execute(rec["action"], params)
        status = "executed" if result["ok"] else "failed"
        self.db.execute(
            "UPDATE recommendations SET status=?, decided_by=?, decision_note=?, result=? WHERE id=?",
            (status, approver, note, json.dumps(result), rec_id))
        self.db.execute("UPDATE anomalies SET status=? WHERE id=?",
                        ("executed" if result["ok"] else "open", rec["anomaly_id"]))
        self.audit("system", f"action_{status}", f"rec:{rec_id}", result)
        return result

    def reject(self, rec_id: int, approver: str, reason: str = "", false_positive: bool = False) -> None:
        rec = self._rec(rec_id, "pending_approval")
        a = self.db.one("SELECT * FROM anomalies WHERE id=?", (rec["anomaly_id"],))
        self.db.execute(
            "UPDATE recommendations SET status='rejected', decided_by=?, decision_note=? WHERE id=?",
            (approver, reason, rec_id))
        self.audit(approver, "rejected", f"rec:{rec_id}", reason)
        if false_positive:
            self.db.execute("UPDATE anomalies SET status='dismissed' WHERE id=?", (a["id"],))
            self._raise_threshold(a["tenant"], a["metric"])
            self.db.execute(
                "INSERT INTO feedback(recommendation_id,tenant,metric,action,root_cause,kind,resolved,note,created) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (rec_id, a["tenant"], a["metric"], rec["action"], "", "false_positive", 0, reason, time.time()))
        else:
            self.db.execute("UPDATE anomalies SET status='open' WHERE id=?", (a["id"],))

    # ---- 6. verify + learn --------------------------------------------
    def evaluate(self, rec_id: int, after_points: int = 3) -> dict:
        rec = self._rec(rec_id, "executed")
        a = self.db.one("SELECT * FROM anomalies WHERE id=?", (rec["anomaly_id"],))
        after = [r["value"] for r in self.db.query(
            "SELECT value FROM metrics WHERE tenant=? AND name=? AND ts>? ORDER BY ts ASC LIMIT ?",
            (a["tenant"], a["metric"], a["ts"], after_points))]
        if len(after) < after_points:
            return {"status": "insufficient_data", "needed": after_points, "have": len(after)}
        base = [r["value"] for r in self.db.query(
            "SELECT value FROM metrics WHERE tenant=? AND name=? AND ts<? ORDER BY ts DESC LIMIT ?",
            (a["tenant"], a["metric"], a["ts"], self.window))]
        limit = self.detector.threshold(a["tenant"], a["metric"])
        zs = [abs(robust_z(base, v)[0]) for v in after]
        resolved = all(z < limit for z in zs)
        inv = self.db.one("SELECT root_cause FROM investigations WHERE anomaly_id=?", (a["id"],))
        self.db.execute(
            "INSERT INTO feedback(recommendation_id,tenant,metric,action,root_cause,kind,resolved,note,created) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (rec_id, a["tenant"], a["metric"], rec["action"], inv["root_cause"] if inv else "",
             "outcome", int(resolved), f"post-action |z|={[round(z, 1) for z in zs]}", time.time()))
        status = "resolved" if resolved else "unresolved"
        self.db.execute("UPDATE recommendations SET status=? WHERE id=?", (status, rec_id))
        self.db.execute("UPDATE anomalies SET status=? WHERE id=?", (status, a["id"]))
        self.audit("system", f"outcome_{status}", f"rec:{rec_id}", {"z": zs})
        return {"status": status, "post_action_z": [round(z, 2) for z in zs]}

    def _raise_threshold(self, tenant: str, metric: str, step: float = 0.5, cap: float = 8.0) -> None:
        new = min(self.detector.threshold(tenant, metric) + step, cap)
        self.db.execute("INSERT OR REPLACE INTO thresholds(tenant,metric,z) VALUES(?,?,?)",
                        (tenant, metric, new))
        self.audit("system", "threshold_raised", f"{tenant}/{metric}", {"z": new})
