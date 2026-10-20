import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    db_path: str = os.getenv("OPSPILOT_DB", "opspilot.db")
    llm_model: str = os.getenv("OPSPILOT_MODEL", "claude-sonnet-5-5")
    api_key: str | None = os.getenv("ANTHROPIC_API_KEY") or None
    window: int = int(os.getenv("OPSPILOT_WINDOW", "30"))      # baseline size (points)
    z_threshold: float = float(os.getenv("OPSPILOT_Z", "6.0"))  # default anomaly threshold
