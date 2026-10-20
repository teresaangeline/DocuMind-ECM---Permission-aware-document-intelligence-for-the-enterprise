"""Run the full loop in your terminal:  python -m opspilot.demo --scenario ocr_degradation --auto"""
import argparse
import json
import os
import tempfile

from .config import Settings
from .db import DB
from .investigator import build_investigator
from .pipeline import OpsPilot
from .simulator import SCENARIOS, generate


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", choices=sorted(SCENARIOS), default="ocr_degradation")
    ap.add_argument("--auto", action="store_true", help="auto-approve (skip the human prompt)")
    args = ap.parse_args()

    settings = Settings()
    path = os.path.join(tempfile.mkdtemp(), "demo.db")
    pilot = OpsPilot(DB(path), build_investigator(settings), settings.window, settings.z_threshold)
    tenant, t0, n = "acme-insurance", 1_700_000_000.0, 40

    print(f"\n[1] COLLECT   ingesting {n} min of telemetry for '{tenant}' (incident injected at the end)")
    pilot.ingest(generate(tenant, t0, n, seed=7, scenario=args.scenario))

    print("[2] DETECT")
    ids = pilot.detect()
    if not ids:
        print("    nothing abnormal."); return
    a = pilot.db.one("SELECT * FROM anomalies WHERE id=?", (ids[0],))
    print(f"    {a['severity'].upper()} anomaly on {a['metric']}: {a['value']:.1f} vs baseline {a['baseline']:.1f}"
          f" (z={a['zscore']:+.1f}); related: {[r['metric'] for r in json.loads(a['related'])]}")

    print("[3] INVESTIGATE (AI)")
    rec_id = pilot.investigate(ids[0])
    inv = pilot.db.one("SELECT * FROM investigations WHERE anomaly_id=?", (ids[0],))
    print(f"    source: {inv['source']}\n    root cause: {inv['root_cause']} (confidence {inv['confidence']:.0%})")
    for e in json.loads(inv["evidence"]):
        print(f"      - {e}")

    rec = pilot.db.one("SELECT * FROM recommendations WHERE id=?", (rec_id,))
    print(f"[4] RECOMMEND {rec['action']} {rec['params']}  [risk: {rec['risk']}]\n    why: {rec['rationale']}")

    ok = True if args.auto else input("[5] APPROVE? (y/n) ").strip().lower().startswith("y")
    if not ok:
        pilot.reject(rec_id, "demo-user", "declined in demo"); print("    rejected - nothing executed."); return
    print(f"[5] HUMAN APPROVED -> {pilot.approve(rec_id, 'demo-user')['message']}")

    print("[6] VERIFY    system recovers; ingesting 3 more minutes of telemetry")
    pilot.ingest(generate(tenant, t0 + n * 60, 3, seed=99))
    print(f"[7] LEARN     {pilot.evaluate(rec_id)}")
    print(f"    feedback stored; action success stats now: {pilot._history(a['metric'])}\n")


if __name__ == "__main__":
    main()
