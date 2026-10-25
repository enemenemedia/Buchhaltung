"""Fachlogik der Rechnungen.

Drei Wege zur Rechnung:
  1. aus einem angenommenen Angebot (aus_angebot)
  2. Leistungen für einen bestehenden Kunden zusammenstellen (anlegen, kunde_modus = 'bestand')
  3. mit neuem Kunden, der dabei in die Kundendatei übernommen wird (anlegen, kunde_modus = 'neu')

Lebenszyklus:
  entwurf – bearbeitbar, noch ohne Rechnungsnummer
  offen   – abgeschlossen: fortlaufende Nummer, XRechnung-XML und PDF gleichnamig im
            Rechnungsordner des Kunden (<Datum>_<Rechnungsnummer>_<Kundennummer>.xml/.pdf)
  bezahlt – mit Zahlungsdatum (kann zurückgesetzt werden)
„Überfällig“ ergibt sich aus faellig_am.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from .. import berechnung, protokoll, zahlen
from .. import positionen as pos
from ..kunden import service as kunden
from ..stammdaten import service as stammdaten

BEREICH = "rechnung"

STATUS = {"entwurf": "Entwurf", "offen": "Offen", "ueberfaellig": "Überfällig", "bezahlt": "Bezahlt"}

KOPF_FELDER = ("rechnungsdatum", "leistung_von", "leistung_bis", "zahlungsziel_tage", "betreff", "anmerkungen",
               "kaeufer_referenz", "bestellnummer", "befreiung_art", "befreiung_grund")

KOPF_BEZEICHNUNG = {
    "kunde": "Kunde", "ansprechpartner": "Ansprechpartner", "rechnungsdatum": "Rechnungsdatum",
    "leistung_von": "Leistungsdatum", "leistung_bis": "Leistungszeitraum bis", "zahlungsziel_tage": "Zahlungsziel (Tage)",
    "faellig_am": "Fällig am", "betreff": "Betreff", "anmerkungen": "Anmerkungen", "kaeufer_referenz": "Käuferreferenz",
    "bestellnummer": "Bestellnummer", "befreiung_art": "Steuerbefreiung", "befreiung_grund": "Befreiungsgrund",
}

NEUKUNDE_FELDER = ("firma", "strasse", "adresszusatz", "plz", "ort", "land", "email", "telefon", "ust_id", "leitweg_id")
NEUKUNDE_AP_FELDER = ("anrede", "vorname", "nachname", "email", "telefon")


class RechnungsFehler(Exception):
    """Fachlicher Fehler, der dem Anwender angezeigt werden kann."""


class ValidierungsFehler(RechnungsFehler):
    def __init__(self, fehler: dict[str, str]):
        super().__init__("Bitte die markierten Felder prüfen.")
        self.fehler = fehler


# ---------------------------------------------------------------------------
# Hilfen
# ---------------------------------------------------------------------------

def heute() -> date:
    return date.today()


def _datum(text) -> date | None:
    try:
        return date.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None


def _de(iso: str | None) -> str:
    return date.fromisoformat(iso).strftime("%d.%m.%Y") if iso else ""


def anzeige_status(r, stichtag: date | None = None) -> str:
    if r["status"] == "offen" and date.fromisoformat(r["faellig_am"]) < (stichtag or heute()):
        return "ueberfaellig"
    return r["status"]


def naechste_nummer(con: sqlite3.Connection, jahr: int) -> str:
    """Fortlaufende Rechnungsnummer je Jahr: RE-2026-0001, RE-2026-0002, …"""
    praefix = f"RE-{jahr}-"
    hoechste = 0
    for (nr,) in con.execute("SELECT nummer FROM rechnungen WHERE nummer LIKE ?", (praefix + "%",)):
        m = re.fullmatch(rf"{re.escape(praefix)}(\d+)", nr or "")
        if m:
            hoechste = max(hoechste, int(m.group(1)))
    return f"{praefix}{hoechste + 1:04d}"


def dateiname(rechnungsdatum: str, nummer: str, kundennummer: str) -> str:
    """Gemeinsamer Name für XML und PDF: Datum, Rechnungsnummer, Kundennummer."""
    return f"{rechnungsdatum}_{nummer}_{kundennummer}"


def empfaenger_text(kunde, ap=None) -> str:
    zeilen = [kunde["firma"]]
    if ap is not None:
        zeilen.append(pos.ansprechpartner_name(ap))
    zeilen += [kunde["strasse"], kunde["adresszusatz"], f"{kunde['plz']} {kunde['ort']}".strip()]
    if kunde["land"] and kunde["land"] != "DE":
        zeilen.append(kunde["land"])
    return "\n".join(z for z in zeilen if z and z.strip())


# ---------------------------------------------------------------------------
# Lesen
# ---------------------------------------------------------------------------

def laden(con: sqlite3.Connection, rechnung_id: int) -> sqlite3.Row:
    r = con.execute(
        "SELECT r.*, k.firma AS kunde_firma, k.kundennummer, k.archiviert_am AS kunde_archiviert, "
        "a.nummer AS angebot_nummer FROM rechnungen r JOIN kunden k ON k.id = r.kunde_id "
        "LEFT JOIN angebote a ON a.id = r.angebot_id WHERE r.id = ?", (rechnung_id,)).fetchone()
    if r is None:
        raise RechnungsFehler("Rechnung nicht gefunden.")
    return r


def positionen(con: sqlite3.Connection, rechnung_id: int) -> list[sqlite3.Row]:
    return con.execute("SELECT * FROM rechnung_positionen WHERE rechnung_id = ? ORDER BY position, id",
                       (rechnung_id,)).fetchall()


def summen(con: sqlite3.Connection, r) -> berechnung.Summen:
    # Rechnungen werden immer netto-basiert berechnet (wie in der XRechnung)
    return berechnung.berechnen(positionen(con, r["id"]), bool(r["kleinunternehmer"]), "netto")


def liste(con: sqlite3.Connection, suche: str = "", status: str = "") -> list[sqlite3.Row]:
    sql = ("SELECT r.*, k.firma AS kunde_firma, k.kundennummer FROM rechnungen r "
           "JOIN kunden k ON k.id = r.kunde_id WHERE 1 = 1")
    params: list = []
    tag = heute().isoformat()
    if status == "ueberfaellig":
        sql += " AND r.status = 'offen' AND r.faellig_am < ?"
        params.append(tag)
    elif status == "offen":
        sql += " AND r.status = 'offen' AND r.faellig_am >= ?"
        params.append(tag)
    elif status in ("entwurf", "bezahlt"):
        sql += " AND r.status = ?"
        params.append(status)
    if suche:
        sql += " AND (r.nummer LIKE ? OR r.betreff LIKE ? OR k.firma LIKE ? OR k.kundennummer LIKE ?)"
        params += [f"%{suche}%"] * 4
    sql += " ORDER BY r.status = 'entwurf' DESC, r.rechnungsdatum DESC, r.id DESC"
    return con.execute(sql, params).fetchall()


def fuer_kunde(con: sqlite3.Connection, kunde_id: int) -> list[sqlite3.Row]:
    return con.execute("SELECT * FROM rechnungen WHERE kunde_id = ? ORDER BY rechnungsdatum DESC, id DESC",
                       (kunde_id,)).fetchall()


def fuer_angebot(con: sqlite3.Connection, angebot_id: int) -> list[sqlite3.Row]:
    return con.execute("SELECT * FROM rechnungen WHERE angebot_id = ? ORDER BY id", (angebot_id,)).fetchall()


def verlauf(con: sqlite3.Connection, rechnung_id: int) -> list[dict]:
    return protokoll.lesen(con, BEREICH, rechnung_id)


def summen_offen(con: sqlite3.Connection) -> dict:
    """Kennzahlen für die Liste: offene und überfällige Beträge."""
    tag = heute().isoformat()
    offen = ueberfaellig = zahlen.parsen("0")
    for r in con.execute("SELECT summe_gesamt, faellig_am FROM rechnungen WHERE status = 'offen'"):
        betrag = zahlen.parsen(r["summe_gesamt"])
        offen += betrag
        if r["faellig_am"] < tag:
            ueberfaellig += betrag
    return {"offen": offen, "ueberfaellig": ueberfaellig}


# ---------------------------------------------------------------------------
# Formular einlesen & prüfen
# ---------------------------------------------------------------------------

@dataclass
class Eingabe:
    kopf: dict
    positionen: list[dict]
    fehler: dict[str, str]
    neukunde: dict | None = None
    neukunde_ap: list[dict] = field(default_factory=list)


def vorgaben(con: sqlite3.Connection, kunde_id: int | None = None) -> dict:
    einstellungen = stammdaten.laden(con)
    tag = heute().isoformat()
    daten = {"kunde_modus": "bestand", "kunde_id": str(kunde_id or ""), "ansprechpartner_id": "",
             "rechnungsdatum": tag, "leistung_von": tag, "leistung_bis": "",
             "zahlungsziel_tage": str(einstellungen["zahlungsziel_tage"]), "betreff": "", "anmerkungen": "",
             "kaeufer_referenz": "", "bestellnummer": "", "befreiung_art": "E", "befreiung_grund": "",
             "neu_land": "DE"}
    if kunde_id:
        k = con.execute("SELECT leitweg_id FROM kunden WHERE id = ?", (kunde_id,)).fetchone()
        daten["kaeufer_referenz"] = k["leitweg_id"] if k else ""
        haupt = con.execute("SELECT id FROM ansprechpartner WHERE kunde_id = ? ORDER BY ist_hauptkontakt DESC, "
                            "position LIMIT 1", (kunde_id,)).fetchone()
        daten["ansprechpartner_id"] = str(haupt["id"]) if haupt else ""
    return daten


def eingabe_pruefen(con: sqlite3.Connection, form) -> Eingabe:
    fehler: dict[str, str] = {}
    g = lambda name: (form.get(name) or "").strip()  # noqa: E731
    kopf = {f: g(f) for f in KOPF_FELDER}
    kopf["kunde_id"], kopf["ansprechpartner_id"] = g("kunde_id"), g("ansprechpartner_id")
    modus = g("kunde_modus") or "bestand"
    e = Eingabe(kopf=kopf, positionen=[], fehler=fehler)

    # --- Kunde -------------------------------------------------------------
    if modus == "neu":
        roh = {f: g(f"neu_{f}") for f in NEUKUNDE_FELDER}
        kd = {f: roh.get(f, "") for f in kunden.KUNDEN_FELDER}
        kd["land"] = (kd["land"] or "DE").upper()
        ap = {f: g(f"neu_ap_{f}") for f in NEUKUNDE_AP_FELDER}
        ap_liste = kunden.ansprechpartner_aus_formular([{**ap, "ist_hauptkontakt": "1"}])
        for feld, meldung in kunden.pruefen(kd, ap_liste).fehler.items():
            fehler["neu_ap_" + feld.split("-")[-1] if feld.startswith("ap-") else "neu_" + feld] = meldung
        for feld, text in (("strasse", "Straße"), ("plz", "PLZ"), ("ort", "Ort")):
            if not kd[feld]:
                fehler.setdefault(f"neu_{feld}", f"Bitte {text} angeben – sie gehört auf die Rechnung.")
        e.neukunde, e.neukunde_ap = kd, ap_liste
        kopf["kunde_id"], kopf["ansprechpartner_id"] = "", ""
    else:
        kunde = None
        if not kopf["kunde_id"].isdigit():
            fehler["kunde_id"] = "Bitte einen Kunden wählen oder einen neuen Kunden eingeben."
        else:
            try:
                kunde = kunden.laden(con, int(kopf["kunde_id"]))
            except kunden.KundenFehler:
                fehler["kunde_id"] = "Der Kunde wurde nicht gefunden."
            else:
                if kunde["archiviert_am"]:
                    fehler["kunde_id"] = "Für archivierte Kunden können keine Rechnungen erstellt werden."
        if kopf["ansprechpartner_id"]:
            ok = kunde is not None and kopf["ansprechpartner_id"].isdigit() and con.execute(
                "SELECT 1 FROM ansprechpartner WHERE id = ? AND kunde_id = ?",
                (int(kopf["ansprechpartner_id"]), kunde["id"])).fetchone()
            if not ok:
                fehler["ansprechpartner_id"] = "Der Ansprechpartner gehört nicht zu diesem Kunden."

    # --- Daten ----------------------------------------------------------------
    r_datum, von, bis = _datum(kopf["rechnungsdatum"]), _datum(kopf["leistung_von"]), None
    if r_datum is None:
        fehler["rechnungsdatum"] = "Bitte ein gültiges Rechnungsdatum angeben."
    if von is None:
        fehler["leistung_von"] = "Bitte das Leistungsdatum angeben (§ 14 UStG)."
    if kopf["leistung_bis"]:
        bis = _datum(kopf["leistung_bis"])
        if bis is None:
            fehler["leistung_bis"] = "Bitte ein gültiges Datum angeben."
        elif von and bis < von:
            fehler["leistung_bis"] = "Das Ende darf nicht vor dem Beginn liegen."
        elif von and bis == von:
            kopf["leistung_bis"] = ""
    if not kopf["zahlungsziel_tage"].isdigit() or not 0 <= int(kopf["zahlungsziel_tage"]) <= 365:
        fehler["zahlungsziel_tage"] = "Bitte eine Zahl zwischen 0 und 365 angeben."
    elif r_datum:
        kopf["faellig_am"] = (r_datum + timedelta(days=int(kopf["zahlungsziel_tage"]))).isoformat()

    for feld, grenze in (("betreff", 200), ("anmerkungen", 5000), ("kaeufer_referenz", 100),
                         ("bestellnummer", 100), ("befreiung_grund", 300)):
        if len(kopf[feld]) > grenze:
            fehler[feld] = f"Höchstens {grenze} Zeichen."
    if kopf["befreiung_art"] not in ("E", "AE"):
        kopf["befreiung_art"] = "E"

    e.positionen = pos.pruefen(form, fehler)
    return e


# ---------------------------------------------------------------------------
# Speichern
# ---------------------------------------------------------------------------

def _neukunde_anlegen(con, datenverzeichnis: Path, e: Eingabe) -> tuple[int, int | None, str]:
    kunde_id = kunden.anlegen(con, datenverzeichnis, e.neukunde, e.neukunde_ap)
    k = kunden.laden(con, kunde_id)
    aps = kunden.ansprechpartner(con, kunde_id)
    return kunde_id, (aps[0]["id"] if aps else None), f"Neuer Kunde {k['kundennummer']} „{k['firma']}“ in die Kundendatei übernommen"


def _kopf_anzeige(con, kopf: dict) -> dict:
    kunde = con.execute("SELECT kundennummer, firma FROM kunden WHERE id = ?", (kopf["kunde_id"],)).fetchone()
    ap = None
    if kopf.get("ansprechpartner_id"):
        ap = con.execute("SELECT * FROM ansprechpartner WHERE id = ?", (kopf["ansprechpartner_id"],)).fetchone()
    return {
        "kunde": f"{kunde['kundennummer']} {kunde['firma']}" if kunde else "",
        "ansprechpartner": pos.ansprechpartner_name(ap) if ap else "",
        "rechnungsdatum": _de(kopf["rechnungsdatum"]), "leistung_von": _de(kopf["leistung_von"]),
        "leistung_bis": _de(kopf.get("leistung_bis") or None), "zahlungsziel_tage": str(kopf["zahlungsziel_tage"]),
        "betreff": kopf["betreff"], "anmerkungen": kopf["anmerkungen"], "kaeufer_referenz": kopf["kaeufer_referenz"],
        "bestellnummer": kopf["bestellnummer"],
        "befreiung_art": {"E": "steuerfrei", "AE": "Reverse Charge"}.get(kopf["befreiung_art"], ""),
        "befreiung_grund": kopf["befreiung_grund"],
    }


def _schreiben(con, rechnung_id: int, e: Eingabe, einstellungen) -> berechnung.Summen:
    kunde = kunden.laden(con, int(e.kopf["kunde_id"]))
    ap = None
    if e.kopf["ansprechpartner_id"]:
        ap = con.execute("SELECT * FROM ansprechpartner WHERE id = ?", (int(e.kopf["ansprechpartner_id"]),)).fetchone()
    klein = bool(einstellungen["kleinunternehmer"])
    s = berechnung.berechnen(e.positionen, klein, "netto")
    k = e.kopf
    con.execute(
        "UPDATE rechnungen SET kunde_id = ?, ansprechpartner_id = ?, rechnungsdatum = ?, leistung_von = ?, "
        "leistung_bis = ?, zahlungsziel_tage = ?, faellig_am = ?, betreff = ?, anmerkungen = ?, kaeufer_referenz = ?, "
        "bestellnummer = ?, befreiung_art = ?, befreiung_grund = ?, kleinunternehmer = ?, empfaenger = ?, "
        "summe_netto = ?, summe_ust = ?, summe_gesamt = ?, geaendert_am = ? WHERE id = ?",
        (kunde["id"], ap["id"] if ap else None, k["rechnungsdatum"], k["leistung_von"], k["leistung_bis"] or None,
         int(k["zahlungsziel_tage"]), k["faellig_am"], k["betreff"], k["anmerkungen"], k["kaeufer_referenz"],
         k["bestellnummer"], k["befreiung_art"], k["befreiung_grund"], int(klein), empfaenger_text(kunde, ap),
         zahlen.speicherform(s.netto), zahlen.speicherform(s.ust), zahlen.speicherform(s.gesamt),
         protokoll.jetzt(), rechnung_id))
    pos.schreiben(con, "rechnung_positionen", "rechnung_id", rechnung_id, e.positionen)
    return s


def _neu(con, e: Eingabe, angebot_id: int | None = None) -> int:
    cur = con.execute(
        "INSERT INTO rechnungen (kunde_id, angebot_id, rechnungsdatum, leistung_von, faellig_am, erstellt_am, "
        "geaendert_am) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (int(e.kopf["kunde_id"]), angebot_id, e.kopf["rechnungsdatum"], e.kopf["leistung_von"],
         e.kopf["faellig_am"], protokoll.jetzt(), protokoll.jetzt()))
    return cur.lastrowid


def anlegen(con: sqlite3.Connection, datenverzeichnis: Path, form) -> int:
    """Neuer Rechnungsentwurf – für einen bestehenden oder einen neuen Kunden."""
    e = eingabe_pruefen(con, form)
    if e.fehler:
        raise ValidierungsFehler(e.fehler)
    hinweise: list[str] = []
    if e.neukunde is not None:
        kunde_id, ap_id, hinweis = _neukunde_anlegen(con, datenverzeichnis, e)
        e.kopf["kunde_id"], e.kopf["ansprechpartner_id"] = str(kunde_id), str(ap_id or "")
        hinweise.append(hinweis)
    einstellungen = stammdaten.laden(con)
    with con:
        rechnung_id = _neu(con, e)
        hinweise += pos.freie_uebernehmen(con, e.positionen, f"Rechnungsentwurf #{rechnung_id}")
        s = _schreiben(con, rechnung_id, e, einstellungen)
        protokoll.schreiben(con, BEREICH, rechnung_id, "angelegt", {
            "felder": {KOPF_BEZEICHNUNG[k]: ["", v] for k, v in _kopf_anzeige(con, e.kopf).items() if v},
            "hinweise": [*pos.als_text(e.positionen), f"Gesamtbetrag {zahlen.geld(s.gesamt)}", *hinweise],
        })
    return rechnung_id


def aendern(con: sqlite3.Connection, datenverzeichnis: Path, rechnung_id: int, form) -> bool:
    alt = laden(con, rechnung_id)
    if alt["status"] != "entwurf":
        raise RechnungsFehler("Abgeschlossene Rechnungen können nicht geändert werden.")
    e = eingabe_pruefen(con, form)
    if e.fehler:
        raise ValidierungsFehler(e.fehler)
    hinweise: list[str] = []
    if e.neukunde is not None:
        kunde_id, ap_id, hinweis = _neukunde_anlegen(con, datenverzeichnis, e)
        e.kopf["kunde_id"], e.kopf["ansprechpartner_id"] = str(kunde_id), str(ap_id or "")
        hinweise.append(hinweis)
    einstellungen = stammdaten.laden(con)
    alte_pos = [dict(p) for p in positionen(con, rechnung_id)]
    alt_kopf = {f: ("" if alt[f] is None else str(alt[f])) for f in (*KOPF_FELDER, "kunde_id", "ansprechpartner_id")}
    with con:
        hinweise += pos.freie_uebernehmen(con, e.positionen, f"Rechnungsentwurf #{rechnung_id}")
        s = _schreiben(con, rechnung_id, e, einstellungen)
        a_alt, a_neu = _kopf_anzeige(con, alt_kopf), _kopf_anzeige(con, e.kopf)
        felder = {KOPF_BEZEICHNUNG[k]: [a_alt[k], a_neu[k]] for k in a_neu if a_alt[k] != a_neu[k]}
        p_alt, p_neu = pos.als_text(alte_pos), pos.als_text(e.positionen)
        if p_alt != p_neu:
            geaendert = [*(f"Entfernt: {t}" for t in p_alt if t not in p_neu),
                         *(f"Hinzugefügt: {t}" for t in p_neu if t not in p_alt)]
            hinweise = (geaendert or ["Reihenfolge der Positionen geändert"]) + hinweise
        if alt["summe_gesamt"] != zahlen.speicherform(s.gesamt):
            felder["Gesamtbetrag"] = [zahlen.geld(alt["summe_gesamt"]), zahlen.geld(s.gesamt)]
        if not felder and not hinweise:
            con.rollback()
            return False
        details: dict = {}
        if felder:
            details["felder"] = felder
        if hinweise:
            details["hinweise"] = hinweise
        protokoll.schreiben(con, BEREICH, rechnung_id, "geaendert", details)
    return True


def aus_angebot(con: sqlite3.Connection, angebot_id: int) -> int:
    """Erstellt einen Rechnungsentwurf aus einem angenommenen Angebot."""
    from ..angebote import service as angebote

    a = angebote.laden(con, angebot_id)
    if a["status"] != "angenommen":
        raise RechnungsFehler("Rechnungen können nur aus angenommenen Angeboten erstellt werden.")
    if a["kunde_archiviert"]:
        raise RechnungsFehler("Der Kunde ist archiviert. Bitte zuerst reaktivieren.")
    vorhanden = fuer_angebot(con, angebot_id)
    if vorhanden:
        r = vorhanden[0]
        raise RechnungsFehler(f"Zu diesem Angebot gibt es bereits die Rechnung "
                              f"{r['nummer'] or 'im Entwurf #' + str(r['id'])}.")
    v = vorgaben(con, a["kunde_id"])
    form = {**v, "kunde_modus": "bestand", "kunde_id": str(a["kunde_id"]),
            "ansprechpartner_id": str(a["ansprechpartner_id"] or ""), "betreff": a["betreff"],
            "anmerkungen": f"Grundlage: Angebot {a['nummer']} vom {_de(a['datum'])}."}
    for i, p in enumerate(angebote.positionen(con, angebot_id)):
        for f in pos.FELDER:
            form[f"pos-{i}-{f}"] = "" if p[f] is None else str(p[f])
    e = eingabe_pruefen(con, form)
    if e.fehler:
        raise ValidierungsFehler(e.fehler)
    einstellungen = stammdaten.laden(con)
    with con:
        rechnung_id = _neu(con, e, angebot_id)
        s = _schreiben(con, rechnung_id, e, einstellungen)
        protokoll.schreiben(con, BEREICH, rechnung_id, "angelegt", {
            "hinweise": [f"Aus Angebot {a['nummer']} erstellt", *pos.als_text(e.positionen),
                         f"Gesamtbetrag {zahlen.geld(s.gesamt)}"]})
        protokoll.schreiben(con, "angebot", angebot_id, "rechnung",
                            {"hinweise": [f"Rechnungsentwurf #{rechnung_id} erstellt"]})
    return rechnung_id


# ---------------------------------------------------------------------------
# Prüfung der XRechnung-Pflichtangaben
# ---------------------------------------------------------------------------

@dataclass
class Pruefpunkt:
    ok: bool
    text: str
    bereich: str          # stammdaten | kunde | rechnung
    hinweis: str = ""


def _kaeufer_email(kunde, ap) -> str:
    return kunde["email"] or (ap["email"] if ap is not None else "")


def pruefliste(con: sqlite3.Connection, r) -> list[Pruefpunkt]:
    """Alles, was für eine gültige XRechnung vorhanden sein muss."""
    punkte: list[Pruefpunkt] = []
    e = stammdaten.laden(con)
    fehlend = stammdaten.fehlende_pflichtangaben(e)
    if fehlend:
        for f in fehlend:
            punkte.append(Pruefpunkt(False, f"Stammdaten: {f.text}", "stammdaten"))
    else:
        punkte.append(Pruefpunkt(True, "Eigene Firmendaten vollständig (Verkäufer, Kontakt, Bankverbindung)", "stammdaten"))

    kunde = kunden.laden(con, r["kunde_id"])
    ap = None
    if r["ansprechpartner_id"]:
        ap = con.execute("SELECT * FROM ansprechpartner WHERE id = ?", (r["ansprechpartner_id"],)).fetchone()
    anschrift_ok = bool(kunde["plz"] and kunde["ort"])
    punkte.append(Pruefpunkt(anschrift_ok, "Kundenanschrift mit PLZ und Ort (BT-52, BT-53)", "kunde",
                             "" if anschrift_ok else "In den Kundendaten ergänzen."))
    mail = _kaeufer_email(kunde, ap)
    punkte.append(Pruefpunkt(bool(mail), "Elektronische Adresse des Kunden (BT-49)", "kunde",
                             f"wird verwendet: {mail}" if mail else
                             "E-Mail-Adresse für den Rechnungsempfang in den Kundendaten eintragen."))
    if kunde["archiviert_am"]:
        punkte.append(Pruefpunkt(False, "Kunde ist archiviert", "kunde", "Kunden zuerst reaktivieren."))

    pos_liste = positionen(con, r["id"])
    punkte.append(Pruefpunkt(bool(pos_liste), "Mindestens eine Rechnungsposition", "rechnung"))
    punkte.append(Pruefpunkt(bool(r["leistung_von"]), "Leistungsdatum bzw. -zeitraum (§ 14 UStG)", "rechnung"))
    referenz = r["kaeufer_referenz"] or kunde["leitweg_id"]
    punkte.append(Pruefpunkt(True, "Käuferreferenz / Leitweg-ID (BT-10)", "rechnung",
                             f"„{referenz}“" if referenz else f"keine angegeben – es wird die Kundennummer "
                                                             f"{kunde['kundennummer']} verwendet"))
    if not e["kleinunternehmer"] and any(zahlen.parsen(p["ust_satz"]) == 0 for p in pos_liste):
        if r["befreiung_art"] == "AE":
            ok = bool(kunde["ust_id"]) and bool(e["ust_id"])
            punkte.append(Pruefpunkt(ok, "Reverse Charge: USt-IdNr. von Verkäufer und Kunde", "kunde",
                                     "" if ok else "USt-IdNr. in Stamm- bzw. Kundendaten ergänzen."))
    return punkte


def bereit(con: sqlite3.Connection, r) -> bool:
    return all(p.ok for p in pruefliste(con, r))


# ---------------------------------------------------------------------------
# Abschließen
# ---------------------------------------------------------------------------

def _verkaeufer(e) -> dict:
    felder = ("firma", "handelsname", "inhaber", "strasse", "adresszusatz", "plz", "ort", "land", "telefon", "email",
              "website", "elektronische_adresse", "kontakt_name", "kontakt_telefon", "kontakt_email", "ust_id",
              "steuernummer", "finanzamt", "registergericht", "registernummer", "kleinunternehmer", "kontoinhaber",
              "iban", "bic", "bank")
    return {f: e[f] for f in felder}


def _kaeufer(kunde, ap) -> dict:
    return {
        "kundennummer": kunde["kundennummer"], "firma": kunde["firma"], "strasse": kunde["strasse"],
        "adresszusatz": kunde["adresszusatz"], "plz": kunde["plz"], "ort": kunde["ort"], "land": kunde["land"],
        "ust_id": kunde["ust_id"], "leitweg_id": kunde["leitweg_id"],
        "elektronische_adresse": _kaeufer_email(kunde, ap),
        "ap_name": " ".join(x for x in (ap["vorname"], ap["nachname"]) if x) if ap is not None else "",
        "ap_email": ap["email"] if ap is not None else "", "ap_telefon": ap["telefon"] if ap is not None else "",
    }


_PRUEFER: dict = {}


def _xml_pruefen(xml: bytes) -> None:
    """Prüft das XML mit dem offiziellen Regelwerk, wenn es eingerichtet ist (XRECHNUNG_ARTEFAKTE)."""
    artefakte = os.environ.get("XRECHNUNG_ARTEFAKTE")
    if not artefakte:
        return
    import sys
    werkzeuge = str(Path(__file__).resolve().parents[2] / "werkzeuge")
    if werkzeuge not in sys.path:
        sys.path.insert(0, werkzeuge)
    from xrechnung_pruefen import Pruefer  # noqa: E402

    if _PRUEFER.get(artefakte) is None:
        _PRUEFER[artefakte] = Pruefer(artefakte)   # Kompilieren dauert einige Sekunden – nur einmal
    erg = _PRUEFER[artefakte].pruefen(xml)
    if not erg.gueltig:
        meldungen = "; ".join(f"{m.regel}: {m.text[:120]}" for m in erg.fehler[:3])
        raise RechnungsFehler(f"Die erzeugte XRechnung ist nicht gültig: {meldungen}")


def abschliessen(con: sqlite3.Connection, datenverzeichnis: Path, rechnung_id: int) -> tuple[Path, Path]:
    """Vergibt die Rechnungsnummer, erzeugt XRechnung (XML) und PDF und legt beide
    gleichnamig im Rechnungsordner des Kunden ab. Danach ist die Rechnung unveränderlich."""
    from . import dokument, xrechnung

    r = laden(con, rechnung_id)
    if r["status"] != "entwurf":
        raise RechnungsFehler("Die Rechnung ist bereits abgeschlossen.")
    offen = [p for p in pruefliste(con, r) if not p.ok]
    if offen:
        raise RechnungsFehler("Für die XRechnung fehlen noch Angaben: " + "; ".join(p.text for p in offen))

    einstellungen = stammdaten.laden(con)
    kunde = kunden.laden(con, r["kunde_id"])
    ap = None
    if r["ansprechpartner_id"]:
        ap = con.execute("SELECT * FROM ansprechpartner WHERE id = ?", (r["ansprechpartner_id"],)).fetchone()
    nummer = naechste_nummer(con, date.fromisoformat(r["rechnungsdatum"]).year)
    name = dateiname(r["rechnungsdatum"], nummer, kunde["kundennummer"])
    relativ = Path("kunden") / kunde["kundennummer"] / "rechnungen" / name
    basis = Path(datenverzeichnis) / relativ
    xml_pfad, pdf_pfad = basis.with_name(name + ".xml"), basis.with_name(name + ".pdf")
    for pfad in (xml_pfad, pdf_pfad):
        if pfad.exists():
            raise RechnungsFehler(f"Die Datei {pfad.name} existiert bereits.")
    basis.parent.mkdir(parents=True, exist_ok=True)

    verkaeufer, kaeufer = _verkaeufer(einstellungen), _kaeufer(kunde, ap)
    klein = bool(einstellungen["kleinunternehmer"])
    pos_liste = [dict(p) for p in positionen(con, rechnung_id)]
    s = berechnung.berechnen(pos_liste, klein, "netto")
    kopf = {**dict(r), "nummer": nummer, "status": "offen", "kleinunternehmer": int(klein),
            "kaeufer_referenz": r["kaeufer_referenz"] or kunde["leitweg_id"] or kunde["kundennummer"],
            "empfaenger": empfaenger_text(kunde, ap), "kaeufer_ust_id": kunde["ust_id"]}

    xml = xrechnung.erzeugen(kopf, pos_liste, verkaeufer, kaeufer)
    _xml_pruefen(xml)
    pdf = dokument.erzeugen(kopf, pos_liste, verkaeufer, datenverzeichnis)
    try:
        with con:
            jetzt = protokoll.jetzt()
            con.execute(
                "UPDATE rechnungen SET nummer = ?, status = 'offen', kleinunternehmer = ?, kaeufer_referenz = ?, "
                "empfaenger = ?, verkaeufer = ?, kaeufer = ?, summe_netto = ?, summe_ust = ?, summe_gesamt = ?, "
                "dateiname = ?, abgeschlossen_am = ?, geaendert_am = ? WHERE id = ? AND status = 'entwurf'",
                (nummer, int(klein), kopf["kaeufer_referenz"], kopf["empfaenger"],
                 json.dumps(verkaeufer, ensure_ascii=False), json.dumps(kaeufer, ensure_ascii=False),
                 zahlen.speicherform(s.netto), zahlen.speicherform(s.ust), zahlen.speicherform(s.gesamt),
                 relativ.as_posix(), jetzt, jetzt, rechnung_id))
            xml_pfad.write_bytes(xml)
            pdf_pfad.write_bytes(pdf)
            protokoll.schreiben(con, BEREICH, rechnung_id, "abgeschlossen", {"hinweise": [
                f"Rechnungsnummer {nummer} vergeben",
                f"XRechnung abgelegt: {relativ.as_posix()}.xml",
                f"PDF abgelegt: {relativ.as_posix()}.pdf",
                f"Gesamtbetrag {zahlen.geld(s.gesamt)}, fällig am {_de(r['faellig_am'])}"]})
            if r["angebot_id"]:
                protokoll.schreiben(con, "angebot", r["angebot_id"], "rechnung",
                                    {"hinweise": [f"Rechnung {nummer} gestellt"]})
    except Exception:
        xml_pfad.unlink(missing_ok=True)
        pdf_pfad.unlink(missing_ok=True)
        raise
    return xml_pfad, pdf_pfad


def vorschau_pdf(con: sqlite3.Connection, datenverzeichnis: Path, rechnung_id: int) -> bytes:
    """PDF-Vorschau eines Entwurfs (mit Wasserzeichen, wird nicht gespeichert)."""
    from . import dokument

    r = laden(con, rechnung_id)
    kunde = kunden.laden(con, r["kunde_id"])
    kopf = {**dict(r), "kaeufer_referenz": r["kaeufer_referenz"] or kunde["leitweg_id"] or kunde["kundennummer"],
            "kaeufer_ust_id": kunde["ust_id"]}
    return dokument.erzeugen(kopf, [dict(p) for p in positionen(con, rechnung_id)],
                             _verkaeufer(stammdaten.laden(con)), datenverzeichnis)


def dateien(datenverzeichnis: Path, r) -> dict[str, Path]:
    if not r["dateiname"]:
        return {}
    basis = (Path(datenverzeichnis) / r["dateiname"]).resolve()
    if Path(datenverzeichnis).resolve() not in basis.parents:
        return {}
    return {"pdf": basis.with_name(basis.name + ".pdf"), "xml": basis.with_name(basis.name + ".xml")}


# ---------------------------------------------------------------------------
# Zahlung, Löschen
# ---------------------------------------------------------------------------

def bezahlt_setzen(con: sqlite3.Connection, rechnung_id: int, datum: str) -> None:
    r = laden(con, rechnung_id)
    if r["status"] != "offen":
        raise RechnungsFehler("Nur offene Rechnungen können als bezahlt markiert werden.")
    d = _datum(datum)
    if d is None:
        raise RechnungsFehler("Bitte ein gültiges Zahlungsdatum angeben.")
    if d < date.fromisoformat(r["rechnungsdatum"]):
        raise RechnungsFehler("Das Zahlungsdatum liegt vor dem Rechnungsdatum.")
    with con:
        con.execute("UPDATE rechnungen SET status = 'bezahlt', bezahlt_am = ?, geaendert_am = ? WHERE id = ?",
                    (d.isoformat(), protokoll.jetzt(), rechnung_id))
        protokoll.schreiben(con, BEREICH, rechnung_id, "status",
                            {"felder": {"Status": ["Offen", "Bezahlt"], "Zahlungseingang": ["", _de(d.isoformat())]}})


def zahlung_zuruecksetzen(con: sqlite3.Connection, rechnung_id: int) -> None:
    r = laden(con, rechnung_id)
    if r["status"] != "bezahlt":
        raise RechnungsFehler("Die Rechnung ist nicht als bezahlt markiert.")
    with con:
        con.execute("UPDATE rechnungen SET status = 'offen', bezahlt_am = NULL, geaendert_am = ? WHERE id = ?",
                    (protokoll.jetzt(), rechnung_id))
        protokoll.schreiben(con, BEREICH, rechnung_id, "status",
                            {"felder": {"Status": ["Bezahlt", "Offen"], "Zahlungseingang": [_de(r["bezahlt_am"]), ""]}})


def loeschen(con: sqlite3.Connection, rechnung_id: int) -> sqlite3.Row:
    r = laden(con, rechnung_id)
    if r["status"] != "entwurf":
        raise RechnungsFehler("Nur Entwürfe können gelöscht werden. Abgeschlossene Rechnungen müssen aufbewahrt werden.")
    with con:
        con.execute("DELETE FROM rechnungen WHERE id = ?", (rechnung_id,))
        protokoll.schreiben(con, BEREICH, rechnung_id, "geloescht",
                            {"hinweise": [f"Entwurf #{rechnung_id} für {r['kunde_firma']}"]})
    return r
