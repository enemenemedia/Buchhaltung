"""Gemeinsames Änderungsprotokoll für alle Module.

Jeder Eintrag hält Zeitpunkt, Benutzer (nur Netzwerkversion), Bereich (z. B. 'kunde', 'stammdaten'),
betroffenes Objekt, Aktion und Details (JSON) fest. Übliche Details:
  {"felder": {"<Bezeichnung>": [alt, neu], ...}}   geänderte Felder
  {"hinweise": ["…", ...]}                         freie Hinweise
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime


def jetzt() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def angemeldeter_benutzer() -> str | None:
    """Name des in der Netzwerkversion angemeldeten Benutzers (sonst None)."""
    from flask import g, has_request_context

    if not has_request_context():
        return None
    b = g.get("benutzer")
    return b["name"] if b else None


def schreiben(con: sqlite3.Connection, bereich: str, objekt_id: int, aktion: str,
              details: dict | None = None) -> None:
    con.execute(
        "INSERT INTO aenderungsprotokoll (zeitpunkt, bereich, objekt_id, aktion, details, benutzer) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (jetzt(), bereich, objekt_id, aktion, json.dumps(details or {}, ensure_ascii=False),
         angemeldeter_benutzer()),
    )


def lesen(con: sqlite3.Connection, bereich: str, objekt_id: int, limit: int | None = None) -> list[dict]:
    sql = ("SELECT * FROM aenderungsprotokoll WHERE bereich = ? AND objekt_id = ? "
           "ORDER BY zeitpunkt DESC, id DESC")
    params: list = [bereich, objekt_id]
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    return [dict(z, details=json.loads(z["details"] or "{}")) for z in con.execute(sql, params)]


def felder_diff(alt, neu: dict, bezeichnungen: dict[str, str]) -> dict[str, list]:
    """Vergleicht Felder und liefert {Bezeichnung: [alt, neu]} für geänderte Werte."""
    return {
        bezeichnungen.get(f, f): [alt[f], neu[f]]
        for f in neu
        if f in bezeichnungen and alt[f] != neu[f]
    }
