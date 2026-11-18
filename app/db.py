"""SQLite-Verbindung, Initialisierung und Verschlüsselung der Datenbank.

Unverschlüsselte Datenbanken werden mit dem sqlite3-Modul von Python geöffnet,
verschlüsselte mit SQLCipher (Paket sqlcipher3-wheels; AES-256, Schlüssel aus dem
Passwort per PBKDF2-HMAC-SHA512 abgeleitet). Das Passwort wird nirgends gespeichert –
nur im Arbeitsspeicher, solange das Programm entsperrt ist.
"""

import os
import sqlite3
from pathlib import Path

from flask import current_app, g

try:  # Verschlüsselung ist optional; ohne SQLCipher bleibt die Datenbank unverschlüsselt
    from sqlcipher3 import dbapi2 as sqlcipher
except ImportError:  # pragma: no cover
    sqlcipher = None

SCHEMA = Path(__file__).with_name("schema.sql")
SQLITE_KOPF = b"SQLite format 3\x00"
VERSCHLUESSELUNG_VERFUEGBAR = sqlcipher is not None
PASSWORT_MIN = 8


class PasswortFalsch(Exception):
    pass


class VerschluesselungsFehler(Exception):
    pass


def ist_verschluesselt(pfad: Path) -> bool:
    """Eine SQLite-Datei beginnt mit „SQLite format 3“; SQLCipher verschlüsselt auch den Kopf."""
    try:
        with open(pfad, "rb") as f:
            kopf = f.read(16)
    except FileNotFoundError:
        return False
    return len(kopf) == 16 and kopf != SQLITE_KOPF


def _pragma_text(wert: str) -> str:
    return "'" + wert.replace("'", "''") + "'"


def _einrichten(con, row) -> None:
    con.row_factory = row
    con.execute("PRAGMA foreign_keys = ON")
    # Netzwerkversion: gleichzeitige Zugriffe warten kurz statt mit „database is locked“ abzubrechen
    con.execute("PRAGMA busy_timeout = 10000")


def verbinden(pfad: Path, passwort: str | None = None):
    if not passwort:
        con = sqlite3.connect(pfad, detect_types=sqlite3.PARSE_DECLTYPES)
        _einrichten(con, sqlite3.Row)
        return con
    if sqlcipher is None:
        raise VerschluesselungsFehler("SQLCipher ist nicht installiert – verschlüsselte Datenbanken "
                                      "können nicht geöffnet werden.")
    con = sqlcipher.connect(str(pfad), detect_types=sqlcipher.PARSE_DECLTYPES)
    try:
        con.execute("PRAGMA cipher_log_level = NONE")
        con.execute(f"PRAGMA key = {_pragma_text(passwort)}")
        con.execute("SELECT count(*) FROM sqlite_master").fetchone()
    except sqlcipher.DatabaseError:
        con.close()
        raise PasswortFalsch("Das Passwort ist falsch.") from None
    _einrichten(con, sqlcipher.Row)
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
        "xml_ausstehend": "TEXT",
    },
    "aenderungsprotokoll": {
        "benutzer": "TEXT",
    },
}


def _spalten_ergaenzen(con) -> None:
    for tabelle, spalten in NACHTRAEGLICHE_SPALTEN.items():
        vorhanden = {z["name"] for z in con.execute(f"PRAGMA table_info({tabelle})")}
        if not vorhanden:
            continue
        for name, definition in spalten.items():
            if name not in vorhanden:
                con.execute(f"ALTER TABLE {tabelle} ADD COLUMN {name} {definition}")


def initialisieren(pfad: Path, passwort: str | None = None) -> None:
    pfad.parent.mkdir(parents=True, exist_ok=True)
    con = verbinden(pfad, passwort)
    try:
        # Erst fehlende Spalten ergänzen, dann das (idempotente) Schema ausführen
        _spalten_ergaenzen(con)
        con.executescript(SCHEMA.read_text(encoding="utf-8"))
        con.commit()
    finally:
        con.close()


def get_db():
    if "db" not in g:
        g.db = verbinden(current_app.config["DATENBANK"], current_app.config.get("DB_PASSWORT"))
    return g.db


def schliessen(_exc=None) -> None:
    con = g.pop("db", None)
    if con is not None:
        con.close()


# ---------------------------------------------------------------------------
# Verschlüsseln, Passwort ändern, Verschlüsselung aufheben
# ---------------------------------------------------------------------------

def passwort_pruefen(passwort: str, wiederholung: str) -> str | None:
    """Fehlermeldung oder None."""
    if len(passwort or "") < PASSWORT_MIN:
        return f"Das Passwort muss mindestens {PASSWORT_MIN} Zeichen lang sein."
    if passwort != wiederholung:
        return "Die beiden Passwörter stimmen nicht überein."
    return None


def _umschreiben(pfad: Path, alt: str | None, neu: str | None) -> None:
    """Schreibt die Datenbank vollständig in eine neue Datei (mit neuem bzw. ohne Passwort),
    prüft sie und ersetzt dann das Original. Bei einem Fehler bleibt das Original unverändert."""
    if sqlcipher is None:
        raise VerschluesselungsFehler("Die Verschlüsselung ist in dieser Installation nicht verfügbar "
                                      "(Paket sqlcipher3-wheels fehlt).")
    pfad = Path(pfad)
    ziel = pfad.with_name(pfad.name + ".umschreiben")
    ziel.unlink(missing_ok=True)
    quelle = sqlcipher.connect(str(pfad))
    try:
        quelle.execute("PRAGMA cipher_log_level = NONE")
        if alt:
            quelle.execute(f"PRAGMA key = {_pragma_text(alt)}")
        try:
            version = quelle.execute("PRAGMA user_version").fetchone()[0]
        except sqlcipher.DatabaseError:
            raise PasswortFalsch("Das Passwort ist falsch.") from None
        quelle.execute("ATTACH DATABASE ? AS ziel KEY ?", (str(ziel), neu or ""))
        quelle.execute("SELECT sqlcipher_export('ziel')")
        quelle.execute(f"PRAGMA ziel.user_version = {int(version)}")
        quelle.commit()
        quelle.execute("DETACH DATABASE ziel")
    except Exception:
        quelle.close()
        ziel.unlink(missing_ok=True)
        raise
    quelle.close()

    # Neue Datei prüfen, bevor sie das Original ersetzt
    try:
        con = verbinden(ziel, neu)
        try:
            if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise VerschluesselungsFehler("Die Prüfung der neuen Datenbankdatei ist fehlgeschlagen.")
        finally:
            con.close()
    except Exception:
        ziel.unlink(missing_ok=True)
        raise
    os.replace(ziel, pfad)
    for rest in (pfad.with_name(pfad.name + "-journal"), pfad.with_name(pfad.name + "-wal"),
                 pfad.with_name(pfad.name + "-shm")):
        rest.unlink(missing_ok=True)


def verschluesseln(pfad: Path, passwort: str) -> None:
    if ist_verschluesselt(pfad):
        raise VerschluesselungsFehler("Die Datenbank ist bereits verschlüsselt.")
    _umschreiben(pfad, None, passwort)


def passwort_aendern(pfad: Path, alt: str, neu: str) -> None:
    if not ist_verschluesselt(pfad):
        raise VerschluesselungsFehler("Die Datenbank ist nicht verschlüsselt.")
    _umschreiben(pfad, alt, neu)


def entschluesseln(pfad: Path, passwort: str) -> None:
    if not ist_verschluesselt(pfad):
        raise VerschluesselungsFehler("Die Datenbank ist nicht verschlüsselt.")
    _umschreiben(pfad, passwort, None)
