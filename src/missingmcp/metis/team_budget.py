"""Durable, fail-closed reservation ledger for digital-team operations.

An operator supplies the exact cycle and ceilings. This module never decides
that a new period has started and never refunds an uncertain external call.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

KEY = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
SCOPE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


class BudgetError(RuntimeError):
    pass


@dataclass(frozen=True)
class Reservation:
    key: str
    scope: str
    cycle: str
    units: int
    state: str
    receipt: str | None


class TeamBudget:
    """One SQLite-backed ledger shared by all Gateway workers on one volume."""

    def __init__(self, db_path: str):
        self.db = sqlite3.connect(db_path, isolation_level=None, timeout=20)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA busy_timeout=20000")
        self.db.execute("""CREATE TABLE IF NOT EXISTS team_budget_reservations (
            key TEXT PRIMARY KEY, scope TEXT NOT NULL, cycle TEXT NOT NULL,
            fingerprint TEXT NOT NULL, units INTEGER NOT NULL,
            state TEXT NOT NULL, receipt TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')))""")
        self.db.execute("""CREATE INDEX IF NOT EXISTS team_budget_scope_cycle
            ON team_budget_reservations(scope,cycle)""")

    def reserve(self, *, key: str, scope: str, cycle: str, fingerprint: str,
                units: int, max_units: int, max_calls: int) -> Reservation:
        if not isinstance(key, str) or not KEY.fullmatch(key):
            raise ValueError("invalid_budget_key")
        if (not isinstance(scope, str) or not SCOPE.fullmatch(scope) or
                not isinstance(cycle, str) or not SCOPE.fullmatch(cycle)):
            raise ValueError("invalid_budget_scope_or_cycle")
        if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
            raise ValueError("invalid_budget_fingerprint")
        if any(not isinstance(x, int) or isinstance(x, bool) or x < 1
               for x in (units, max_units, max_calls)):
            raise ValueError("invalid_budget_limit")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                "SELECT * FROM team_budget_reservations WHERE key=?", (key,)).fetchone()
            if row is not None:
                if (row["scope"], row["cycle"], row["fingerprint"], row["units"]) != (
                        scope, cycle, fingerprint, units):
                    raise BudgetError("budget_idempotency_conflict")
            else:
                total, calls = self.db.execute(
                    "SELECT COALESCE(SUM(units),0),COUNT(*) FROM team_budget_reservations "
                    "WHERE scope=? AND cycle=?", (scope, cycle)).fetchone()
                if calls >= max_calls or total + units > max_units:
                    raise BudgetError("budget_exhausted")
                self.db.execute(
                    "INSERT INTO team_budget_reservations(key,scope,cycle,fingerprint,units,state) "
                    "VALUES (?,?,?,?,?,'RESERVED')",
                    (key, scope, cycle, fingerprint, units))
                row = self.db.execute(
                    "SELECT * FROM team_budget_reservations WHERE key=?", (key,)).fetchone()
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        return Reservation(row["key"], row["scope"], row["cycle"],
                           row["units"], row["state"], row["receipt"])

    def record_receipt(self, key: str, receipt: str) -> Reservation:
        if not isinstance(receipt, str) or not receipt or len(receipt) > 256:
            raise ValueError("invalid_budget_receipt")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                "SELECT * FROM team_budget_reservations WHERE key=?", (key,)).fetchone()
            if row is None:
                raise BudgetError("reservation_missing")
            if row["receipt"] is not None and row["receipt"] != receipt:
                raise BudgetError("budget_receipt_conflict")
            self.db.execute("UPDATE team_budget_reservations SET state='RECEIPTED',receipt=? "
                            "WHERE key=?", (receipt, key))
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        return Reservation(row["key"], row["scope"], row["cycle"],
                           row["units"], "RECEIPTED", receipt)

    def close(self):
        self.db.close()
