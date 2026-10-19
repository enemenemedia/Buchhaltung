"""Fachlogik der Kundenverwaltung.

Alle Funktionen erhalten eine offene SQLite-Verbindung, damit sie sowohl
aus der Weboberfläche als auch aus Tests oder Skripten nutzbar sind.
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

AUFBEWAHRUNG_JAHRE = 10

KUNDEN_FELDER = (
    "firma", "strasse", "adresszusatz", "plz", "ort", "land",
    "ust_id", "steuernummer", "leitweg_id", "email", "telefon", "notizen",
)

ANSPRECHPARTNER_FELDER = (
    "anrede", "titel", "vorname", "nachname", "funktion",
    "email", "telefon", "mobil",
)

FELD_BEZEICHNUNG = {
    "firma": "Firma", "strasse": "Straße und Hausnummer", "adresszusatz": "Adresszusatz",
    "plz": "PLZ", "ort": "Ort", "land": "Land", "ust_id": "USt-IdNr.",
    "steuernummer": "Steuernummer", "leitweg_id": "Leitweg-ID", "email": "E-Mail",
    "telefon": "Telefon", "notizen": "Notizen",
}

# Unterordner, die pro Kunde neben der Datenbank angelegt werden.
KUNDEN_UNTERORDNER = ("angebote", "rechnungen", "dokumente")

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_LEITWEG = re.compile(r"^[0-9]{2,12}(-[A-Za-z0-9]{1,30})?-[0-9]{2}$")


class KundenFehler(Exception):
    """Fachlicher Fehler, der dem Anwender angezeigt werden kann."""


@dataclass
class Pruefergebnis:
    fehler: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.fehler


# ---------------------------------------------------------------------------
# Aufbewahrungsfrist
# ---------------------------------------------------------------------------

def loeschbar_ab(archiviert_am: date, weitere_daten: list[date] | None = None) -> date:
    """Erster Tag, an dem ein Kunde endgültig gelöscht werden darf.

    Die Frist beginnt mit dem Schluss des Kalenderjahres des letzten
    relevanten Ereignisses (Archivierung bzw. später auch letzter Beleg)
    und läuft 10 Jahre. Beispiel: archiviert am 19.10.2026 -> Frist endet
    am 31.12.2036 -> löschbar ab 01.01.2037.
    """
    daten = [archiviert_am, *(weitere_daten or [])]
    letztes_jahr = max(d.year for d in daten)
    return date(letztes_jahr + AUFBEWAHRUNG_JAHRE + 1, 1, 1)


def ist_loeschbar(kunde: sqlite3.Row | dict, heute: date | None = None) -> bool:
    if not kunde["archiviert_am"] or not kunde["loeschbar_ab"]:
        return False
    heute = heute or date.today()
    return heute >= date.fromisoformat(kunde["loeschbar_ab"])


# ---------------------------------------------------------------------------
# Validierung
# ---------------------------------------------------------------------------

def _bereinigen(daten: dict, felder: tuple[str, ...]) -> dict:
    return {f: (daten.get(f) or "").strip() for f in felder}


def pruefen(kunde: dict, ansprechpartner: list[dict]) -> Pruefergebnis:
    erg = Pruefergebnis()
    if not kunde["firma"]:
        erg.fehler["firma"] = "Bitte den Firmennamen angeben."
    if not re.fullmatch(r"[A-Z]{2}", kunde["land"]):
        erg.fehler["land"] = "Ländercode mit zwei Großbuchstaben angeben (z. B. DE)."
    if kunde["land"] == "DE" and kunde["plz"] and not re.fullmatch(r"\d{5}", kunde["plz"]):
        erg.fehler["plz"] = "Deutsche Postleitzahlen haben fünf Ziffern."
    if kunde["email"] and not _EMAIL.match(kunde["email"]):
        erg.fehler["email"] = "Die E-Mail-Adresse ist ungültig."
    if kunde["leitweg_id"] and not _LEITWEG.match(kunde["leitweg_id"]):
        erg.fehler["leitweg_id"] = "Format der Leitweg-ID ungültig (z. B. 04011000-1234512345-06)."
    for i, ap in enumerate(ansprechpartner):
        if not ap["nachname"]:
            erg.fehler[f"ap-{i}-nachname"] = "Bitte den Nachnamen angeben."
        if ap["email"] and not _EMAIL.match(ap["email"]):
            erg.fehler[f"ap-{i}-email"] = "Die E-Mail-Adresse ist ungültig."
    return erg


def ansprechpartner_aus_formular(eintraege: list[dict]) -> list[dict]:
    """Bereinigt Formularzeilen; vollständig leere Zeilen werden verworfen."""
    ergebnis = []
    for e in eintraege:
        ap = _bereinigen(e, ANSPRECHPARTNER_FELDER)
        if not any(ap.values()):
            continue
        ap["id"] = int(e["id"]) if str(e.get("id") or "").isdigit() else None
        ap["ist_hauptkontakt"] = bool(e.get("ist_hauptkontakt"))
        ergebnis.append(ap)
    # Genau ein Hauptkontakt, sofern es Ansprechpartner gibt.
    if ergebnis and sum(a["ist_hauptkontakt"] for a in ergebnis) != 1:
        haupt = next((i for i, a in enumerate(ergebnis) if a["ist_hauptkontakt"]), 0)
        for i, a in enumerate(ergebnis):
            a["ist_hauptkontakt"] = i == haupt
    return ergebnis


# ---------------------------------------------------------------------------
# Lesen
# ---------------------------------------------------------------------------

def liste(con: sqlite3.Connection, suche: str = "", status: str = "aktiv") -> list[sqlite3.Row]:
    sql = """
        SELECT k.*,
               (SELECT trim(a.vorname || ' ' || a.nachname) FROM ansprechpartner a
                 WHERE a.kunde_id = k.id ORDER BY a.ist_hauptkontakt DESC, a.position LIMIT 1)
                   AS hauptkontakt,
               (SELECT count(*) FROM ansprechpartner a WHERE a.kunde_id = k.id) AS anzahl_ap
          FROM kunden k
         WHERE 1 = 1
    """
    params: list = []
    if status == "aktiv":
        sql += " AND k.archiviert_am IS NULL"
    elif status == "archiviert":
        sql += " AND k.archiviert_am IS NOT NULL"
    if suche:
        muster = f"%{suche}%"
        sql += """ AND (k.firma LIKE ? OR k.kundennummer LIKE ? OR k.ort LIKE ? OR k.plz LIKE ?
                   OR EXISTS (SELECT 1 FROM ansprechpartner a WHERE a.kunde_id = k.id
                              AND (a.vorname || ' ' || a.nachname LIKE ? OR a.email LIKE ?)))"""
        params += [muster] * 6
    sql += " ORDER BY k.firma COLLATE NOCASE"
    return con.execute(sql, params).fetchall()


def laden(con: sqlite3.Connection, kunde_id: int) -> sqlite3.Row:
    kunde = con.execute("SELECT * FROM kunden WHERE id = ?", (kunde_id,)).fetchone()
    if kunde is None:
        raise KundenFehler("Kunde nicht gefunden.")
    return kunde


def ansprechpartner(con: sqlite3.Connection, kunde_id: int) -> list[sqlite3.Row]:
    return con.execute(
        "SELECT * FROM ansprechpartner WHERE kunde_id = ? ORDER BY ist_hauptkontakt DESC, position, id",
        (kunde_id,),
    ).fetchall()


def protokoll(con: sqlite3.Connection, kunde_id: int) -> list[dict]:
    zeilen = con.execute(
        "SELECT * FROM aenderungsprotokoll WHERE bereich = 'kunde' AND objekt_id = ? "
        "ORDER BY zeitpunkt DESC, id DESC",
        (kunde_id,),
    ).fetchall()
    return [dict(z, details=json.loads(z["details"] or "{}")) for z in zeilen]


# ---------------------------------------------------------------------------
# Schreiben
# ---------------------------------------------------------------------------

def _jetzt() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _protokollieren(con, kunde_id: int, aktion: str, details: dict | None = None) -> None:
    con.execute(
        "INSERT INTO aenderungsprotokoll (zeitpunkt, bereich, objekt_id, aktion, details) "
        "VALUES (?, 'kunde', ?, ?, ?)",
        (_jetzt(), kunde_id, aktion, json.dumps(details or {}, ensure_ascii=False)),
    )


def kundenordner(datenverzeichnis: Path, kundennummer: str) -> Path:
    return Path(datenverzeichnis) / "kunden" / kundennummer


def _ordner_anlegen(datenverzeichnis: Path, kundennummer: str) -> None:
    basis = kundenordner(datenverzeichnis, kundennummer)
    for unter in KUNDEN_UNTERORDNER:
        (basis / unter).mkdir(parents=True, exist_ok=True)


def _ap_name(ap) -> str:
    return " ".join(p for p in (ap["vorname"], ap["nachname"]) if p) or "(ohne Namen)"


def _ansprechpartner_speichern(con, kunde_id: int, neue: list[dict]) -> list[str]:
    """Gleicht Ansprechpartner ab und liefert eine Beschreibung der Änderungen."""
    vorhanden = {r["id"]: r for r in ansprechpartner(con, kunde_id)}
    aenderungen: list[str] = []
    behalten: set[int] = set()

    for pos, ap in enumerate(neue):
        werte = [ap[f] for f in ANSPRECHPARTNER_FELDER] + [int(ap["ist_hauptkontakt"]), pos]
        alt = vorhanden.get(ap["id"]) if ap["id"] else None
        if alt is None:
            con.execute(
                f"INSERT INTO ansprechpartner (kunde_id, {', '.join(ANSPRECHPARTNER_FELDER)}, "
                f"ist_hauptkontakt, position) VALUES (?, {', '.join('?' * (len(werte)))})",
                [kunde_id, *werte],
            )
            aenderungen.append(f"Ansprechpartner hinzugefügt: {_ap_name(ap)}")
            continue
        behalten.add(alt["id"])
        geaendert = [f for f in ANSPRECHPARTNER_FELDER if alt[f] != ap[f]]
        if bool(alt["ist_hauptkontakt"]) != ap["ist_hauptkontakt"] and ap["ist_hauptkontakt"]:
            aenderungen.append(f"Neuer Hauptkontakt: {_ap_name(ap)}")
        if geaendert:
            aenderungen.append(f"Ansprechpartner geändert: {_ap_name(ap)} ({', '.join(geaendert)})")
        con.execute(
            f"UPDATE ansprechpartner SET {', '.join(f + ' = ?' for f in ANSPRECHPARTNER_FELDER)}, "
            "ist_hauptkontakt = ?, position = ? WHERE id = ?",
            [*werte, alt["id"]],
        )

    for ap_id, alt in vorhanden.items():
        if ap_id not in behalten:
            con.execute("DELETE FROM ansprechpartner WHERE id = ?", (ap_id,))
            aenderungen.append(f"Ansprechpartner entfernt: {_ap_name(alt)}")
    return aenderungen


def anlegen(con, datenverzeichnis: Path, daten: dict, ap_liste: list[dict]) -> int:
    kunde = _bereinigen(daten, KUNDEN_FELDER)
    kunde["land"] = kunde["land"].upper() or "DE"
    erg = pruefen(kunde, ap_liste)
    if not erg.ok:
        raise ValidierungsFehler(erg.fehler)

    jetzt = _jetzt()
    with con:
        cur = con.execute(
            f"INSERT INTO kunden (kundennummer, {', '.join(KUNDEN_FELDER)}, erstellt_am, geaendert_am) "
            f"VALUES (?, {', '.join('?' * len(KUNDEN_FELDER))}, ?, ?)",
            [f"tmp-{uuid.uuid4()}", *kunde.values(), jetzt, jetzt],
        )
        kunde_id = cur.lastrowid
        kundennummer = f"K{kunde_id:05d}"
        con.execute("UPDATE kunden SET kundennummer = ? WHERE id = ?", (kundennummer, kunde_id))
        _ansprechpartner_speichern(con, kunde_id, ap_liste)
        _protokollieren(con, kunde_id, "angelegt", {"kundennummer": kundennummer, "firma": kunde["firma"]})
    _ordner_anlegen(datenverzeichnis, kundennummer)
    return kunde_id


def aendern(con, kunde_id: int, daten: dict, ap_liste: list[dict]) -> bool:
    alt = laden(con, kunde_id)
    if alt["archiviert_am"]:
        raise KundenFehler("Archivierte Kunden können nicht geändert werden. Bitte zuerst reaktivieren.")
    kunde = _bereinigen(daten, KUNDEN_FELDER)
    kunde["land"] = kunde["land"].upper() or "DE"
    erg = pruefen(kunde, ap_liste)
    if not erg.ok:
        raise ValidierungsFehler(erg.fehler)

    felder_diff = {
        FELD_BEZEICHNUNG[f]: [alt[f], kunde[f]] for f in KUNDEN_FELDER if alt[f] != kunde[f]
    }
    with con:
        ap_diff = _ansprechpartner_speichern(con, kunde_id, ap_liste)
        if not felder_diff and not ap_diff:
            return False
        con.execute(
            f"UPDATE kunden SET {', '.join(f + ' = ?' for f in KUNDEN_FELDER)}, geaendert_am = ? WHERE id = ?",
            [*kunde.values(), _jetzt(), kunde_id],
        )
        details: dict = {}
        if felder_diff:
            details["felder"] = felder_diff
        if ap_diff:
            details["ansprechpartner"] = ap_diff
        _protokollieren(con, kunde_id, "geaendert", details)
    return True


def archivieren(con, kunde_id: int, heute: date | None = None) -> date:
    kunde = laden(con, kunde_id)
    if kunde["archiviert_am"]:
        raise KundenFehler("Der Kunde ist bereits archiviert.")
    heute = heute or date.today()
    # TODO: Sobald Rechnungen/Angebote existieren, deren Belegdaten als
    # weitere_daten übergeben, damit die Frist ab dem letzten Beleg läuft.
    frist = loeschbar_ab(heute)
    with con:
        con.execute(
            "UPDATE kunden SET archiviert_am = ?, loeschbar_ab = ?, geaendert_am = ? WHERE id = ?",
            (heute.isoformat(), frist.isoformat(), _jetzt(), kunde_id),
        )
        _protokollieren(con, kunde_id, "archiviert", {"loeschbar_ab": frist.isoformat()})
    return frist


def reaktivieren(con, kunde_id: int) -> None:
    kunde = laden(con, kunde_id)
    if not kunde["archiviert_am"]:
        raise KundenFehler("Der Kunde ist nicht archiviert.")
    with con:
        con.execute(
            "UPDATE kunden SET archiviert_am = NULL, loeschbar_ab = NULL, geaendert_am = ? WHERE id = ?",
            (_jetzt(), kunde_id),
        )
        _protokollieren(con, kunde_id, "reaktiviert")


def loeschen(con, datenverzeichnis: Path, kunde_id: int, heute: date | None = None) -> None:
    kunde = laden(con, kunde_id)
    if not kunde["archiviert_am"]:
        raise KundenFehler("Nur archivierte Kunden können gelöscht werden.")
    if not ist_loeschbar(kunde, heute):
        frist = date.fromisoformat(kunde["loeschbar_ab"]).strftime("%d.%m.%Y")
        raise KundenFehler(f"Die Aufbewahrungsfrist läuft noch. Löschen ist ab dem {frist} möglich.")
    with con:
        con.execute("DELETE FROM kunden WHERE id = ?", (kunde_id,))
        _protokollieren(con, kunde_id, "geloescht",
                        {"kundennummer": kunde["kundennummer"], "firma": kunde["firma"]})
    ordner = kundenordner(datenverzeichnis, kunde["kundennummer"])
    if ordner.exists():
        shutil.rmtree(ordner)


class ValidierungsFehler(KundenFehler):
    def __init__(self, fehler: dict[str, str]):
        super().__init__("Bitte die markierten Felder prüfen.")
        self.fehler = fehler
