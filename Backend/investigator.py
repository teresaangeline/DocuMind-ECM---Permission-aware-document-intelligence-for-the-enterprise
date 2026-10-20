"""Stages 3 & 4: AI investigation + recommendation.

Two interchangeable investigators:
  * LLMInvestigator   - Claude reasons over structured evidence, returns strict JSON.
  * RuleBasedInvestigator - deterministic fallback (also the offline demo / test double).

Guardrails: the LLM output is validated against the action registry. Anything
invalid or any API failure degrades gracefully to the rule-based path.
Past outcomes (feedback table) are injected as evidence so recommendations
improve over time: actions that repeatedly failed for a metric are down-ranked.
"""
import json
import re
from dataclasses import dataclass, field

from . import actions

SYSTEM_PROMPT = """You are an SRE assistant for an enterprise content-processing platform \
(document ingestion, OCR/capture, workflow queues, search indexing).
You receive JSON evidence about ONE incident. Decide the most likely root cause and propose
exactly ONE remediation from `allowed_actions`. Prefer actions with better `history` success rates.
Be honest about uncertainty. Respond with ONLY a JSON object:
{"root_cause": str, "confidence": number 0-1, "hypotheses": [str], "evidence": [str],
 "action": str, "params": object matching the action's params, "rationale": str}"""


@dataclass
class Finding:
    root_cause: str
    confidence: float
    hypotheses: list[str]
    evidence: list[str]
    action: str
    params: dict
    rationale: str
    source: str = "rules"


def _direction(z: float) -> str:
    return "high" if z > 0 else "low"


def _describe(m: dict) -> str:
    return f"{m['metric']} = {m['value']:.1f} vs baseline {m['baseline']:.1f} (z={m['zscore']:+.1f})"


class RuleBasedInvestigator:
    def investigate(self, ctx: dict) -> Finding:
        p, tenant = ctx["primary"], ctx["tenant"]
        sig = {p["metric"]: _direction(p["zscore"])}
        for r in ctx["related"]:
            sig[r["metric"]] = _direction(r["zscore"])
        evidence = [_describe(p)] + [_describe(r) for r in ctx["related"]]
        healthy = [m for m in ("ingestion_rate", "ocr_failure_rate", "queue_latency_s", "indexing_lag_s")
                   if m not in sig]
        if healthy:
            evidence.append("Normal: " + ", ".join(healthy))

        T = tenant
        if sig.get("ingestion_rate") == "high" and ("queue_latency_s" in sig or "indexing_lag_s" in sig):
            cause, hyps, conf = "Ingestion surge exceeding processing capacity", [
                "Batch/bulk upload from customer", "Workers under-provisioned for current load"], 0.8
            cands = [("scale_workers", {"service": "processing", "delta": 2}),
                     ("pause_ingestion", {"tenant": T, "minutes": 15})]
        elif sig.get("ocr_failure_rate") == "high" and "ingestion_rate" not in sig:
            cause, hyps, conf = "OCR/capture engine degraded (volume is normal, failures spiked)", [
                "Bad scan batch or unsupported format", "OCR worker memory leak / stale model"], 0.7
            cands = [("restart_worker", {"service": "ocr"}),
                     ("notify_oncall", {"message": f"OCR failure spike for {T}"})]
        elif sig.get("queue_latency_s") == "high" and "ingestion_rate" not in sig:
            cause, hyps, conf = "Stuck queue consumer (latency up while intake is normal)", [
                "Poison message blocking a consumer", "Deadlocked worker"], 0.65
            cands = [("restart_worker", {"service": "queue-consumer"}),
                     ("scale_workers", {"service": "queue-consumer", "delta": 1})]
        elif sig.get("indexing_lag_s") == "high":
            cause, hyps, conf = "Search indexer backlog", [
                "Indexer throughput below ingest rate", "Corrupt index segment"], 0.6
            cands = [("scale_workers", {"service": "indexer", "delta": 2}),
                     ("reindex_tenant", {"tenant": T})]
        elif sig.get("ingestion_rate") == "low":
            cause, hyps, conf = "Upstream feed interruption (intake dropped)", [
                "Customer connector down", "Credential/network failure"], 0.55
            cands = [("notify_oncall", {"message": f"Ingestion dropped for {T}"})]
        else:
            cause, hyps, conf = "Unclassified anomaly", ["Insufficient signal to determine cause"], 0.3
            cands = [("notify_oncall", {"message": f"Unclassified anomaly on {p['metric']} for {T}"})]

        poor = {a for a, s in ctx["history"].items() if s["n"] >= 2 and s["success_rate"] < 0.34}
        chosen = next((c for c in cands if c[0] not in poor), cands[-1])
        if chosen is not cands[0]:
            evidence.append(f"History: '{cands[0][0]}' failed repeatedly for this metric; using alternative.")
            conf = max(0.3, conf - 0.1)
        action, params = chosen
        rationale = (f"{actions.REGISTRY[action].description}. Risk: {actions.REGISTRY[action].risk}. "
                     f"Expected to restore {p['metric']} toward baseline.")
        return Finding(cause, conf, hyps, evidence, action, params, rationale, "rules")


def _parse_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON object in model output")
    return json.loads(m.group(0))


class LLMInvestigator:
    def __init__(self, api_key: str, model: str, fallback: RuleBasedInvestigator | None = None):
        self.api_key, self.model = api_key, model
        self.fallback = fallback or RuleBasedInvestigator()

    def investigate(self, ctx: dict) -> Finding:
        try:
            import anthropic  # lazy: the app runs without the SDK/key
            client = anthropic.Anthropic(api_key=self.api_key)
            payload = {**ctx, "allowed_actions": actions.catalogue()}
            resp = client.messages.create(
                model=self.model, max_tokens=900, system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": json.dumps(payload)}],
            )
            text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
            d = _parse_json(text)
            actions.validate(d["action"], d["params"])          # guardrail
            return Finding(
                root_cause=str(d["root_cause"]),
                confidence=max(0.0, min(1.0, float(d["confidence"]))),
                hypotheses=[str(h) for h in d.get("hypotheses", [])],
                evidence=[str(e) for e in d.get("evidence", [])],
                action=d["action"], params=d["params"],
                rationale=str(d.get("rationale", "")), source=f"llm:{self.model}",
            )
        except Exception as exc:
            f = self.fallback.investigate(ctx)
            f.source = f"rules (llm unavailable: {type(exc).__name__})"
            return f


def build_investigator(settings) -> "RuleBasedInvestigator | LLMInvestigator":
    if settings.api_key:
        return LLMInvestigator(settings.api_key, settings.llm_model)
    return RuleBasedInvestigator()
