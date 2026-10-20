"""FastAPI layer: REST API + approval dashboard.   Run: uvicorn opspilot.main:app --reload"""
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import actions
from .config import Settings
from .db import DB
from .investigator import build_investigator
from .pipeline import OpsPilot
from .simulator import SCENARIOS, generate

STATIC = Path(__file__).parent / "static"


class MetricIn(BaseModel):
    tenant: str
    name: str
    ts: float
    value: float


class DecisionIn(BaseModel):
    approver: str
    reason: str = ""
    false_positive: bool = False


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    pilot = OpsPilot(DB(settings.db_path), build_investigator(settings),
                     settings.window, settings.z_threshold)
    app = FastAPI(title="OpsPilot", version="0.1.0",
                  description="Human-in-the-loop agentic operations for content pipelines")
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    def guard(fn, *a, **k):
        try:
            return fn(*a, **k)
        except KeyError as e:
            raise HTTPException(404, str(e))
        except ValueError as e:
            raise HTTPException(409, str(e))

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/health")
    def health():
        return {"status": "ok", "llm": bool(settings.api_key)}

    @app.post("/metrics")
    def ingest(metrics: list[MetricIn]):
        return {"ingested": pilot.ingest([m.model_dump() for m in metrics])}

    @app.post("/detect")
    def detect():
        return {"anomaly_ids": pilot.detect()}

    @app.get("/anomalies")
    def anomalies():
        rows = pilot.db.query("SELECT * FROM anomalies ORDER BY id DESC")
        for r in rows:
            r["related"] = json.loads(r["related"] or "[]")
        return rows

    @app.post("/anomalies/{aid}/investigate")
    def investigate(aid: int):
        return {"recommendation_id": guard(pilot.investigate, aid)}

    @app.get("/recommendations")
    def recommendations():
        rows = pilot.db.query(
            "SELECT r.*, i.root_cause, i.confidence, i.hypotheses, i.evidence, i.source "
            "FROM recommendations r LEFT JOIN investigations i ON i.anomaly_id = r.anomaly_id "
            "ORDER BY r.id DESC")
        for r in rows:
            for k in ("params", "hypotheses", "evidence", "result"):
                r[k] = json.loads(r[k]) if r.get(k) else None
        return rows

    @app.post("/recommendations/{rid}/approve")
    def approve(rid: int, body: DecisionIn):
        return guard(pilot.approve, rid, body.approver, body.reason)

    @app.post("/recommendations/{rid}/reject")
    def reject(rid: int, body: DecisionIn):
        guard(pilot.reject, rid, body.approver, body.reason, body.false_positive)
        return {"status": "rejected"}

    @app.post("/recommendations/{rid}/evaluate")
    def evaluate(rid: int):
        return guard(pilot.evaluate, rid)

    @app.get("/actions")
    def action_catalogue():
        return actions.catalogue()

    @app.get("/audit")
    def audit():
        return pilot.db.query("SELECT * FROM audit ORDER BY id DESC LIMIT 200")

    # ---- demo helpers (used by the dashboard buttons) -----------------
    @app.post("/demo/seed")
    def demo_seed(scenario: str = "ocr_degradation", tenant: str = "acme-insurance"):
        if scenario not in SCENARIOS:
            raise HTTPException(422, f"scenario must be one of {sorted(SCENARIOS)}")
        last = pilot.db.one("SELECT MAX(ts) t FROM metrics WHERE tenant=?", (tenant,))["t"]
        start = (last + 60) if last else 1_700_000_000.0
        return {"ingested": pilot.ingest(generate(tenant, start, 40, seed=int(start) % 1000, scenario=scenario))}

    @app.post("/demo/recover")
    def demo_recover(tenant: str = "acme-insurance"):
        last = pilot.db.one("SELECT MAX(ts) t FROM metrics WHERE tenant=?", (tenant,))["t"]
        if last is None:
            raise HTTPException(404, "no data for tenant")
        return {"ingested": pilot.ingest(generate(tenant, last + 60, 3, seed=99))}

    return app


app = create_app()
