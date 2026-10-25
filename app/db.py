"""SQLite-Verbindung und Initialisierung."""

import sqlite3
from pathlib import Path

from flask import current_app, g

SCHEMA = Path(__file__).with_name("schema.sql")


def verbinden(pfad: Path) -> sqlite3.Connection:
    con = sqlite3.connect(pfad, detect_types=sqlite3.PARSE_DECLTYPES)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


# Spalten, die nach der ersten Version hinzugekommen sind. CREATE TABLE IF NOT EXISTS
# legt sie in bestehenden Datenbanken nicht an, daher werden sie hier ergänzt.
NACHTRAEGLICHE_SPALTEN = {
    "stammdaten": {
        "preisausweis": "TEXT NOT NULL DEFAULT 'netto' CHECK (preisausweis IN ('netto', 'brutto'))",
        "angebot_gueltigkeit_tage": "INTEGER NOT NULL DEFAULT 30",
    },
    "rechnungen": {
        "art": "TEXT NOT NULL DEFAULT 'rechnung' CHECK (art IN ('rechnung', 'korrektur', 'storno'))",
        "bezug_id": "INTEGER REFERENCES rechnungen (id)",
        "korrektur_grund": "TEXT NOT NULL DEFAULT ''",
        "vorausbezahlt": "TEXT NOT NULL DEFAULT '0'",
    },
}


def _spalten_ergaenzen(con: sqlite3.Connection) -> None:
    for tabelle, spalten in NACHTRAEGLICHE_SPALTEN.items():
        vorhanden = {z["name"] for z in con.execute(f"PRAGMA table_info({tabelle})")}
        if not vorhanden:
            continue
        for name, definition in spalten.items():
            if name not in vorhanden:
                con.execute(f"ALTER TABLE {tabelle} ADD COLUMN {name} {definition}")


def initialisieren(pfad: Path) -> None:
    pfad.parent.mkdir(parents=True, exist_ok=True)
    con = verbinden(pfad)
    try:
        # Erst fehlende Spalten ergänzen, dann das (idempotente) Schema ausführen
        _spalten_ergaenzen(con)
        con.executescript(SCHEMA.read_text(encoding="utf-8"))
        con.commit()
    finally:
        con.close()


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = verbinden(current_app.config["DATENBANK"])
    return g.db


def schliessen(_exc=None) -> None:
    con = g.pop("db", None)
    if con is not None:
        con.close()
