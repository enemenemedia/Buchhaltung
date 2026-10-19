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


def initialisieren(pfad: Path) -> None:
    pfad.parent.mkdir(parents=True, exist_ok=True)
    con = verbinden(pfad)
    try:
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
