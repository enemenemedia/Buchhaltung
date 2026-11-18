"""Benutzer und Rollen der Netzwerkversion.

Rollen:
  admin        – alles, dazu Benutzerverwaltung, Einstellungen (Verschlüsselung) und Stammdaten
  buchhaltung  – erfassen, ändern, festschreiben, Rechnungen abschließen
  lesen        – nur ansehen und herunterladen (Listen, Belege, EÜR, PDFs, CSV)

Passwörter werden nur als Hash gespeichert (werkzeug, scrypt). Nach fünf Fehlversuchen
ist ein Konto fünf Minuten gesperrt.
"""

from __future__ import annotations

import re
import secrets
import sqlite3
from datetime import datetime, timedelta

from werkzeug.security import check_password_hash, generate_password_hash

from .. import protokoll

ROLLEN = {"admin": "Administrator", "buchhaltung": "Buchhaltung", "lesen": "Nur lesen"}
ROLLEN_TEXT = {
    "admin": "alles, einschließlich Benutzer, Stammdaten und Verschlüsselung",
    "buchhaltung": "erfassen, ändern, festschreiben, Rechnungen abschließen",
    "lesen": "nur ansehen und herunterladen",
}
BEREICH = "benutzer"
PASSWORT_MIN = 10
MAX_FEHLVERSUCHE = 5
SPERRZEIT = timedelta(minutes=5)
_NAME = re.compile(r"^[A-Za-z0-9._@-]{2,40}$")
# Vergleichs-Hash für unbekannte Namen: die Prüfung dauert gleich lang, Namen lassen sich nicht erraten
_ATTRAPPE = generate_password_hash(secrets.token_hex(16))


class BenutzerFehler(ValueError):
    pass


class AnmeldeFehler(Exception):
    pass


def _jetzt() -> datetime:
    return datetime.now().replace(microsecond=0)


def passwort_regel(passwort: str, wiederholung: str | None = None) -> None:
    if len(passwort or "") < PASSWORT_MIN:
        raise BenutzerFehler(f"Das Passwort muss mindestens {PASSWORT_MIN} Zeichen lang sein.")
    if wiederholung is not None and passwort != wiederholung:
        raise BenutzerFehler("Die beiden Passwörter stimmen nicht überein.")


def anzahl(con: sqlite3.Connection) -> int:
    return con.execute("SELECT COUNT(*) FROM benutzer").fetchone()[0]


def alle(con: sqlite3.Connection) -> list:
    return con.execute("SELECT * FROM benutzer ORDER BY aktiv DESC, name COLLATE NOCASE").fetchall()


def laden(con: sqlite3.Connection, benutzer_id: int):
    return con.execute("SELECT * FROM benutzer WHERE id = ?", (benutzer_id,)).fetchone()


def _aktive_admins(con, ausser: int | None = None) -> int:
    return con.execute("SELECT COUNT(*) FROM benutzer WHERE rolle = 'admin' AND aktiv = 1 AND id IS NOT ?",
                       (ausser,)).fetchone()[0]


def anlegen(con: sqlite3.Connection, name: str, anzeigename: str, rolle: str, passwort: str,
            wiederholung: str | None = None) -> int:
    name = (name or "").strip()
    if not _NAME.match(name):
        raise BenutzerFehler("Der Anmeldename muss 2–40 Zeichen lang sein (Buchstaben, Ziffern, . _ @ -).")
    if rolle not in ROLLEN:
        raise BenutzerFehler("Unbekannte Rolle.")
    passwort_regel(passwort, wiederholung)
    if con.execute("SELECT 1 FROM benutzer WHERE name = ?", (name,)).fetchone():
        raise BenutzerFehler(f"Den Anmeldenamen „{name}“ gibt es bereits.")
    cur = con.execute(
        "INSERT INTO benutzer (name, anzeigename, rolle, passwort_hash, sitzung, angelegt_am) VALUES (?, ?, ?, ?, ?, ?)",
        (name, (anzeigename or "").strip()[:80], rolle, generate_password_hash(passwort), secrets.token_hex(16),
         protokoll.jetzt()))
    protokoll.schreiben(con, BEREICH, cur.lastrowid, "angelegt",
                        {"felder": {"Anmeldename": [None, name], "Rolle": [None, ROLLEN[rolle]]}})
    return cur.lastrowid


def aendern(con: sqlite3.Connection, benutzer_id: int, anzeigename: str, rolle: str, aktiv: bool) -> None:
    alt = laden(con, benutzer_id)
    if alt is None:
        raise BenutzerFehler("Benutzer nicht gefunden.")
    if rolle not in ROLLEN:
        raise BenutzerFehler("Unbekannte Rolle.")
    if alt["rolle"] == "admin" and alt["aktiv"] and (rolle != "admin" or not aktiv) and not _aktive_admins(con, benutzer_id):
        raise BenutzerFehler("Es muss mindestens ein aktiver Administrator bleiben.")
    neu = {"anzeigename": (anzeigename or "").strip()[:80], "rolle": rolle, "aktiv": 1 if aktiv else 0}
    felder = {}
    if alt["anzeigename"] != neu["anzeigename"]:
        felder["Anzeigename"] = [alt["anzeigename"], neu["anzeigename"]]
    if alt["rolle"] != rolle:
        felder["Rolle"] = [ROLLEN[alt["rolle"]], ROLLEN[rolle]]
    if alt["aktiv"] != neu["aktiv"]:
        felder["Aktiv"] = ["ja" if alt["aktiv"] else "nein", "ja" if aktiv else "nein"]
    if not felder:
        return
    # Rollenwechsel oder Deaktivieren beendet bestehende Anmeldungen
    sitzung = secrets.token_hex(16) if ("Rolle" in felder or "Aktiv" in felder) else alt["sitzung"]
    con.execute("UPDATE benutzer SET anzeigename = ?, rolle = ?, aktiv = ?, sitzung = ? WHERE id = ?",
                (neu["anzeigename"], rolle, neu["aktiv"], sitzung, benutzer_id))
    protokoll.schreiben(con, BEREICH, benutzer_id, "geaendert", {"felder": felder})


def passwort_setzen(con: sqlite3.Connection, benutzer_id: int, passwort: str,
                    wiederholung: str | None = None, *, alt: str | None = None) -> str:
    """Setzt ein neues Passwort; mit alt= wird das bisherige Passwort geprüft. Liefert die neue Sitzungskennung."""
    b = laden(con, benutzer_id)
    if b is None:
        raise BenutzerFehler("Benutzer nicht gefunden.")
    if alt is not None and not check_password_hash(b["passwort_hash"], alt):
        raise BenutzerFehler("Das bisherige Passwort ist falsch.")
    passwort_regel(passwort, wiederholung)
    sitzung = secrets.token_hex(16)
    con.execute("UPDATE benutzer SET passwort_hash = ?, sitzung = ?, fehlversuche = 0, gesperrt_bis = NULL "
                "WHERE id = ?", (generate_password_hash(passwort), sitzung, benutzer_id))
    protokoll.schreiben(con, BEREICH, benutzer_id, "passwort",
                        {"hinweise": ["selbst geändert" if alt is not None else "durch Administrator neu gesetzt"]})
    return sitzung


def anmelden(con: sqlite3.Connection, name: str, passwort: str):
    """Prüft Name und Passwort; liefert die Benutzerzeile oder wirft AnmeldeFehler."""
    b = con.execute("SELECT * FROM benutzer WHERE name = ?", ((name or "").strip(),)).fetchone()
    if b is None:
        check_password_hash(_ATTRAPPE, passwort or "")
        raise AnmeldeFehler("Anmeldename oder Passwort ist falsch.")
    jetzt = _jetzt()
    if b["gesperrt_bis"] and datetime.fromisoformat(b["gesperrt_bis"]) > jetzt:
        raise AnmeldeFehler("Zu viele Fehlversuche – das Konto ist für einige Minuten gesperrt.")
    if not check_password_hash(b["passwort_hash"], passwort or ""):
        versuche = b["fehlversuche"] + 1
        sperre = None
        if versuche >= MAX_FEHLVERSUCHE:
            versuche, sperre = 0, (jetzt + SPERRZEIT).isoformat(sep=" ")
        con.execute("UPDATE benutzer SET fehlversuche = ?, gesperrt_bis = ? WHERE id = ?", (versuche, sperre, b["id"]))
        con.commit()
        raise AnmeldeFehler("Anmeldename oder Passwort ist falsch.")
    if not b["aktiv"]:
        raise AnmeldeFehler("Dieses Konto ist deaktiviert.")
    con.execute("UPDATE benutzer SET fehlversuche = 0, gesperrt_bis = NULL, letzte_anmeldung = ? WHERE id = ?",
                (jetzt.isoformat(sep=" "), b["id"]))
    con.commit()
    return laden(con, b["id"])


def protokoll_gesamt(con: sqlite3.Connection, limit: int = 300, benutzer: str | None = None) -> list[dict]:
    """Letzte Einträge des Änderungsprotokolls über alle Bereiche (für Administratoren)."""
    import json

    sql = "SELECT * FROM aenderungsprotokoll"
    params: list = []
    if benutzer:
        sql += " WHERE benutzer = ?"
        params.append(benutzer)
    sql += " ORDER BY zeitpunkt DESC, id DESC LIMIT ?"
    params.append(limit)
    return [dict(z, details=json.loads(z["details"] or "{}")) for z in con.execute(sql, params)]
