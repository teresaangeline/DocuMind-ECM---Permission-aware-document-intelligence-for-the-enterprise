"""Tiny SQLite wrapper. Swap for Postgres/SQLAlchemy in production."""
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS metrics(
  id INTEGER PRIMARY KEY, tenant TEXT, name TEXT, ts REAL, value REAL);
CREATE INDEX IF NOT EXISTS ix_metrics ON metrics(tenant, name, ts);

CREATE TABLE IF NOT EXISTS anomalies(
  id INTEGER PRIMARY KEY, tenant TEXT, metric TEXT, ts REAL, value REAL,
  baseline REAL, zscore REAL, severity TEXT, related TEXT, status TEXT DEFAULT 'open');

CREATE TABLE IF NOT EXISTS investigations(
  id INTEGER PRIMARY KEY, anomaly_id INTEGER, root_cause TEXT, confidence REAL,
  hypotheses TEXT, evidence TEXT, source TEXT, created REAL);

CREATE TABLE IF NOT EXISTS recommendations(
  id INTEGER PRIMARY KEY, anomaly_id INTEGER, action TEXT, params TEXT,
  rationale TEXT, risk TEXT, status TEXT, decided_by TEXT, decision_note TEXT,
  result TEXT, created REAL);

CREATE TABLE IF NOT EXISTS feedback(
  id INTEGER PRIMARY KEY, recommendation_id INTEGER, tenant TEXT, metric TEXT,
  action TEXT, root_cause TEXT, kind TEXT, resolved INTEGER, note TEXT, created REAL);

CREATE TABLE IF NOT EXISTS thresholds(
  tenant TEXT, metric TEXT, z REAL, PRIMARY KEY(tenant, metric));

CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY, ts REAL, actor TEXT, event TEXT, ref TEXT, detail TEXT);
"""


class DB:
    def __init__(self, path: str):
        self.path = path
        conn = self._conn()
        try:
            with conn:
                conn.executescript(SCHEMA)
        finally:
            conn.close()

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        return c

    def execute(self, sql: str, params: tuple = ()) -> int:
        conn = self._conn()
        try:
            with conn:
                return conn.execute(sql, params).lastrowid
        finally:
            conn.close()

    def executemany(self, sql: str, rows: list[tuple]) -> None:
        conn = self._conn()
        try:
            with conn:
                conn.executemany(sql, rows)
        finally:
            conn.close()

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        conn = self._conn()
        try:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()

    def one(self, sql: str, params: tuple = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None
