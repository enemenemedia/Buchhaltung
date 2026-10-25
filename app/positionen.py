"""Gemeinsame Positionslogik für Angebote und Rechnungen.

Positionen werden im Formular als Felder pos-<n>-<feld> übertragen. Sie können
aus dem Leistungsverzeichnis stammen (leistung_id gesetzt) oder frei eingegeben
werden; freie Positionen werden auf Wunsch ins Leistungsverzeichnis übernommen.
"""

from __future__ import annotations

import re
import sqlite3

from . import zahlen
from .kunden import service as kunden
from .leistungen import service as leistungen

FELDER = ("leistung_id", "leistungsnummer", "bezeichnung", "beschreibung",
          "einheit_code", "menge", "einzelpreis", "ust_satz")

MAX_POSITIONEN = 200


def ansprechpartner_name(ap) -> str:
    return " ".join(t for t in (ap["anrede"], ap["titel"], ap["vorname"], ap["nachname"]) if t)


def aus_formular(form) -> list[dict]:
    """Liest Positionszeilen in der Reihenfolge des Formulars; leere Zeilen entfallen."""
    indizes: list[int] = []
    for schluessel in form.keys():
        m = re.fullmatch(r"pos-(\d+)-bezeichnung", schluessel)
        if m and int(m.group(1)) not in indizes:
            indizes.append(int(m.group(1)))
    zeilen = []
    for i in indizes:
        z = {f: (form.get(f"pos-{i}-{f}") or "").strip() for f in FELDER}
        z["uebernehmen"] = bool(form.get(f"pos-{i}-uebernehmen"))
        z["index"] = i
        if not any(z[f] for f in ("bezeichnung", "beschreibung", "einzelpreis")):
            continue
        zeilen.append(z)
    return zeilen


def pruefen(form, fehler: dict[str, str]) -> list[dict]:
    """Prüft alle Positionen; Fehler werden unter pos-<n>-<feld> in `fehler` eingetragen."""
    zeilen = aus_formular(form)
    if not zeilen:
        fehler["positionen"] = "Bitte mindestens eine Leistung hinzufügen."
    elif len(zeilen) > MAX_POSITIONEN:
        fehler["positionen"] = f"Höchstens {MAX_POSITIONEN} Positionen."
    zuordnung = {"bezeichnung": "bezeichnung", "beschreibung": "beschreibung", "art": "einheit_code",
                 "einheit_code": "einheit_code", "preis": "einzelpreis", "anzahl": "menge", "ust_satz": "ust_satz"}
    geprueft = []
    for p in zeilen:
        i = p["index"]
        daten, pf = leistungen.normalisieren({
            "nummer": "", "bezeichnung": p["bezeichnung"], "beschreibung": p["beschreibung"],
            "art": leistungen.ART_NACH_CODE.get(p["einheit_code"], ""), "einheit_code": p["einheit_code"],
            "preis": p["einzelpreis"], "anzahl": p["menge"], "ust_satz": p["ust_satz"],
        })
        for feld, meldung in pf.items():
            if feld in zuordnung:
                fehler.setdefault(f"pos-{i}-{zuordnung[feld]}", meldung.replace("Anzahl", "Menge"))
        geprueft.append({
            "index": i,
            "leistung_id": int(p["leistung_id"]) if p["leistung_id"].isdigit() else None,
            "leistungsnummer": p["leistungsnummer"][:30],
            "bezeichnung": daten["bezeichnung"], "beschreibung": daten["beschreibung"],
            "einheit_code": daten["einheit_code"] or p["einheit_code"],
            "menge": daten["anzahl"], "einzelpreis": daten["preis"], "ust_satz": daten["ust_satz"],
            "uebernehmen": p["uebernehmen"],
        })
    return geprueft


def freie_uebernehmen(con: sqlite3.Connection, positionen: list[dict], herkunft: str) -> list[str]:
    """Legt frei eingegebene Positionen im Leistungsverzeichnis an bzw. verknüpft eine
    vorhandene Leistung mit gleicher Bezeichnung und Einheit. Liefert Protokollhinweise."""
    hinweise = []
    for p in positionen:
        if p["leistung_id"] is not None:
            vorhanden = con.execute("SELECT nummer FROM leistungen WHERE id = ?", (p["leistung_id"],)).fetchone()
            if vorhanden is None:
                p["leistung_id"] = None
            else:
                p["leistungsnummer"] = vorhanden["nummer"]
                continue
        if not p["uebernehmen"]:
            continue
        gleiche = leistungen.gleiche_leistung(con, p["bezeichnung"], p["einheit_code"])
        if gleiche is not None:
            p["leistung_id"], p["leistungsnummer"] = gleiche["id"], gleiche["nummer"]
            continue
        neu_id = leistungen.anlegen(con, {
            "bezeichnung": p["bezeichnung"], "beschreibung": p["beschreibung"],
            "art": leistungen.ART_NACH_CODE[p["einheit_code"]], "einheit_code": p["einheit_code"],
            "preis": p["einzelpreis"], "anzahl": p["menge"], "ust_satz": p["ust_satz"],
        }, hinweis=f"Aus {herkunft} übernommen", commit=False)
        neu = leistungen.laden(con, neu_id)
        p["leistung_id"], p["leistungsnummer"] = neu_id, neu["nummer"]
        hinweise.append(f"Neue Leistung {neu['nummer']} „{neu['bezeichnung']}“ ins Leistungsverzeichnis übernommen")
    return hinweise


def als_text(positionen) -> list[str]:
    return [f"{zahlen.menge(p['menge'])} {leistungen.einheit_name(p['einheit_code'], p['menge'])} "
            f"{p['bezeichnung']} à {zahlen.geld(p['einzelpreis'])}" for p in positionen]


def schreiben(con: sqlite3.Connection, tabelle: str, fremdschluessel: str, beleg_id: int, positionen) -> None:
    """Ersetzt alle Positionen eines Belegs (nur für Entwürfe; Trigger schützen abgeschlossene)."""
    assert tabelle in ("angebot_positionen", "rechnung_positionen")
    con.execute(f"DELETE FROM {tabelle} WHERE {fremdschluessel} = ?", (beleg_id,))
    for nr, p in enumerate(positionen, start=1):
        con.execute(
            f"INSERT INTO {tabelle} ({fremdschluessel}, position, leistung_id, leistungsnummer, bezeichnung, "
            "beschreibung, einheit_code, menge, einzelpreis, ust_satz) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (beleg_id, nr, p["leistung_id"], p["leistungsnummer"], p["bezeichnung"], p["beschreibung"],
             p["einheit_code"], p["menge"], p["einzelpreis"], p["ust_satz"]),
        )


def fuer_editor(zeilen) -> list[dict]:
    """Gespeicherte Positionen in Formularwerte (deutsche Zahlen) umwandeln."""
    return [{**dict(p), "leistung_id": "" if p["leistung_id"] is None else str(p["leistung_id"]),
             "menge": zahlen.menge(p["menge"]), "einzelpreis": zahlen.geld(p["einzelpreis"], mit_waehrung=False)}
            for p in zeilen]


def kunden_auswahl(con: sqlite3.Connection) -> list[dict]:
    """Aktive Kunden mit Ansprechpartnern für die Formulare."""
    return [{
        "id": k["id"], "nummer": k["kundennummer"], "firma": k["firma"],
        "ort": f"{k['plz']} {k['ort']}".strip(), "leitweg_id": k["leitweg_id"], "email": k["email"],
        "ansprechpartner": [{"id": a["id"], "name": ansprechpartner_name(a) or "(ohne Namen)",
                             "haupt": bool(a["ist_hauptkontakt"])}
                            for a in kunden.ansprechpartner(con, k["id"])],
    } for k in kunden.liste(con, status="aktiv")]


def leistungen_auswahl(con: sqlite3.Connection) -> list[dict]:
    """Aktive Leistungen für die Suche im Formular."""
    return [{
        "id": l["id"], "nummer": l["nummer"], "bezeichnung": l["bezeichnung"],
        "beschreibung": l["beschreibung"], "kategorie": l["kategorie"], "art": l["art"],
        "einheit_code": l["einheit_code"], "einheit": leistungen.einheit_name(l["einheit_code"]),
        "preis": l["preis"], "anzahl": l["anzahl"], "ust_satz": l["ust_satz"],
    } for l in leistungen.liste(con, status="aktiv")]
