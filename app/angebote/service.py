"""Fachlogik der Angebote.

Lebenszyklus:
  entwurf  – frei bearbeitbar, Positionen aus dem Leistungsverzeichnis oder frei
  offen    – abgeschlossen: Daten eingefroren, PDF im Kundenordner abgelegt
  angenommen / abgelehnt – Ergebnis; kann wieder auf „offen“ gesetzt werden
„Abgelaufen“ ist kein gespeicherter Status, sondern ergibt sich aus gueltig_bis.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from .. import berechnung, protokoll, zahlen
from .. import positionen as pos
from ..kunden import service as kunden
from ..leistungen import service as leistungen
from ..stammdaten import service as stammdaten

BEREICH = "angebot"

STATUS = {
    "entwurf": "Entwurf",
    "offen": "Offen",
    "abgelaufen": "Abgelaufen",
    "angenommen": "Angenommen",
    "abgelehnt": "Abgelehnt",
}

POSITIONSFELDER = pos.FELDER

KOPF_BEZEICHNUNG = {
    "kunde": "Kunde", "ansprechpartner": "Ansprechpartner", "datum": "Angebotsdatum",
    "gueltig_bis": "Gültig bis", "betreff": "Betreff", "anmerkungen": "Anmerkungen",
}



class AngebotsFehler(Exception):
    """Fachlicher Fehler, der dem Anwender angezeigt werden kann."""


class ValidierungsFehler(AngebotsFehler):
    def __init__(self, fehler: dict[str, str]):
        super().__init__("Bitte die markierten Felder prüfen.")
        self.fehler = fehler


# ---------------------------------------------------------------------------
# Hilfen
# ---------------------------------------------------------------------------

def heute() -> date:
    return date.today()


def anzeige_status(angebot, stichtag: date | None = None) -> str:
    """Status für die Anzeige inkl. „abgelaufen“."""
    stichtag = stichtag or heute()
    if angebot["status"] in ("entwurf", "offen") and date.fromisoformat(angebot["gueltig_bis"]) < stichtag:
        return "abgelaufen"
    return angebot["status"]


def tage_gueltig(angebot, stichtag: date | None = None) -> int:
    return (date.fromisoformat(angebot["gueltig_bis"]) - (stichtag or heute())).days


def naechste_nummer(con: sqlite3.Connection, jahr: int) -> str:
    """Fortlaufende Nummer je Jahr: AN-2026-0001, AN-2026-0002, …"""
    praefix = f"AN-{jahr}-"
    hoechste = 0
    for (nr,) in con.execute("SELECT nummer FROM angebote WHERE nummer LIKE ?", (praefix + "%",)):
        m = re.fullmatch(rf"{re.escape(praefix)}(\d+)", nr)
        if m:
            hoechste = max(hoechste, int(m.group(1)))
    return f"{praefix}{hoechste + 1:04d}"



def empfaenger_text(kunde, ap=None) -> str:
    zeilen = [kunde["firma"]]
    if ap is not None:
        zeilen.append(ansprechpartner_name(ap))
    zeilen += [kunde["strasse"], kunde["adresszusatz"], f"{kunde['plz']} {kunde['ort']}".strip()]
    if kunde["land"] and kunde["land"] != "DE":
        zeilen.append(kunde["land"])
    return "\n".join(z for z in zeilen if z and z.strip())


def preisausweis(einstellungen) -> str:
    """Kleinunternehmer weisen immer Gesamtpreise (ohne USt) aus."""
    if einstellungen["kleinunternehmer"]:
        return "brutto"
    return einstellungen["preisausweis"] if einstellungen["preisausweis"] in ("netto", "brutto") else "netto"


# ---------------------------------------------------------------------------
# Lesen
# ---------------------------------------------------------------------------

def laden(con: sqlite3.Connection, angebot_id: int) -> sqlite3.Row:
    a = con.execute(
        "SELECT a.*, k.firma AS kunde_firma, k.kundennummer, k.archiviert_am AS kunde_archiviert "
        "FROM angebote a JOIN kunden k ON k.id = a.kunde_id WHERE a.id = ?", (angebot_id,)).fetchone()
    if a is None:
        raise AngebotsFehler("Angebot nicht gefunden.")
    return a


def positionen(con: sqlite3.Connection, angebot_id: int) -> list[sqlite3.Row]:
    return con.execute("SELECT * FROM angebot_positionen WHERE angebot_id = ? ORDER BY position, id",
                       (angebot_id,)).fetchall()


def summen(con: sqlite3.Connection, angebot) -> berechnung.Summen:
    return berechnung.berechnen(positionen(con, angebot["id"]), bool(angebot["kleinunternehmer"]),
                                angebot["preisausweis"])


def liste(con: sqlite3.Connection, suche: str = "", status: str = "") -> list[sqlite3.Row]:
    sql = ("SELECT a.*, k.firma AS kunde_firma, k.kundennummer FROM angebote a "
           "JOIN kunden k ON k.id = a.kunde_id WHERE 1 = 1")
    params: list = []
    tag = heute().isoformat()
    if status == "abgelaufen":
        sql += " AND a.status IN ('entwurf', 'offen') AND a.gueltig_bis < ?"
        params.append(tag)
    elif status in ("entwurf", "offen"):
        sql += " AND a.status = ? AND a.gueltig_bis >= ?"
        params += [status, tag]
    elif status in ("angenommen", "abgelehnt"):
        sql += " AND a.status = ?"
        params.append(status)
    if suche:
        sql += " AND (a.nummer LIKE ? OR a.betreff LIKE ? OR k.firma LIKE ? OR k.kundennummer LIKE ?)"
        params += [f"%{suche}%"] * 4
    sql += " ORDER BY a.datum DESC, a.id DESC"
    return con.execute(sql, params).fetchall()


def fuer_kunde(con: sqlite3.Connection, kunde_id: int) -> list[sqlite3.Row]:
    return con.execute("SELECT * FROM angebote WHERE kunde_id = ? ORDER BY datum DESC, id DESC",
                       (kunde_id,)).fetchall()


def verlauf(con: sqlite3.Connection, angebot_id: int) -> list[dict]:
    return protokoll.lesen(con, BEREICH, angebot_id)




# ---------------------------------------------------------------------------
# Formular einlesen & prüfen
# ---------------------------------------------------------------------------


@dataclass
class Eingabe:
    kopf: dict
    positionen: list[dict]
    fehler: dict[str, str]


def _datum(text: str) -> date | None:
    try:
        return date.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def eingabe_pruefen(con: sqlite3.Connection, form) -> Eingabe:
    fehler: dict[str, str] = {}
    kopf = {
        "kunde_id": (form.get("kunde_id") or "").strip(),
        "ansprechpartner_id": (form.get("ansprechpartner_id") or "").strip(),
        "datum": (form.get("datum") or "").strip(),
        "gueltig_bis": (form.get("gueltig_bis") or "").strip(),
        "betreff": (form.get("betreff") or "").strip(),
        "anmerkungen": (form.get("anmerkungen") or "").strip(),
    }

    kunde = None
    if not kopf["kunde_id"].isdigit():
        fehler["kunde_id"] = "Bitte einen Kunden wählen."
    else:
        try:
            kunde = kunden.laden(con, int(kopf["kunde_id"]))
        except kunden.KundenFehler:
            fehler["kunde_id"] = "Der Kunde wurde nicht gefunden."
        else:
            if kunde["archiviert_am"]:
                fehler["kunde_id"] = "Für archivierte Kunden können keine Angebote erstellt werden."
    if kopf["ansprechpartner_id"]:
        ap_ok = kunde is not None and kopf["ansprechpartner_id"].isdigit() and con.execute(
            "SELECT 1 FROM ansprechpartner WHERE id = ? AND kunde_id = ?",
            (int(kopf["ansprechpartner_id"]), kunde["id"])).fetchone()
        if not ap_ok:
            fehler["ansprechpartner_id"] = "Der Ansprechpartner gehört nicht zu diesem Kunden."

    d_datum, d_gueltig = _datum(kopf["datum"]), _datum(kopf["gueltig_bis"])
    if d_datum is None:
        fehler["datum"] = "Bitte ein gültiges Datum angeben."
    if d_gueltig is None:
        fehler["gueltig_bis"] = "Bitte ein Ablaufdatum angeben."
    elif d_datum and d_gueltig < d_datum:
        fehler["gueltig_bis"] = "Das Ablaufdatum darf nicht vor dem Angebotsdatum liegen."
    if len(kopf["betreff"]) > 200:
        fehler["betreff"] = "Der Betreff darf höchstens 200 Zeichen haben."
    if len(kopf["anmerkungen"]) > 5000:
        fehler["anmerkungen"] = "Die Anmerkungen dürfen höchstens 5000 Zeichen haben."

    geprueft = pos.pruefen(form, fehler)
    return Eingabe(kopf=kopf, positionen=geprueft, fehler=fehler)


# ---------------------------------------------------------------------------
# Speichern
# ---------------------------------------------------------------------------



def _kopf_anzeige(con, kopf: dict) -> dict:
    """Lesbare Kopfwerte für das Protokoll."""
    kunde = con.execute("SELECT kundennummer, firma FROM kunden WHERE id = ?", (kopf["kunde_id"],)).fetchone()
    ap = None
    if kopf.get("ansprechpartner_id"):
        ap = con.execute("SELECT * FROM ansprechpartner WHERE id = ?", (kopf["ansprechpartner_id"],)).fetchone()
    return {
        "kunde": f"{kunde['kundennummer']} {kunde['firma']}" if kunde else "",
        "ansprechpartner": ansprechpartner_name(ap) if ap else "",
        "datum": date.fromisoformat(kopf["datum"]).strftime("%d.%m.%Y"),
        "gueltig_bis": date.fromisoformat(kopf["gueltig_bis"]).strftime("%d.%m.%Y"),
        "betreff": kopf["betreff"], "anmerkungen": kopf["anmerkungen"],
    }


def _schreiben(con, angebot_id: int, e: Eingabe, einstellungen) -> berechnung.Summen:
    """Schreibt Kopf, Empfänger-Kopie, Positionen und Summen eines Entwurfs."""
    kunde = kunden.laden(con, int(e.kopf["kunde_id"]))
    ap = None
    if e.kopf["ansprechpartner_id"]:
        ap = con.execute("SELECT * FROM ansprechpartner WHERE id = ?", (int(e.kopf["ansprechpartner_id"]),)).fetchone()
    klein = bool(einstellungen["kleinunternehmer"])
    s = berechnung.berechnen(e.positionen, klein, preisausweis(einstellungen))
    con.execute(
        "UPDATE angebote SET kunde_id = ?, ansprechpartner_id = ?, datum = ?, gueltig_bis = ?, betreff = ?, "
        "anmerkungen = ?, preisausweis = ?, kleinunternehmer = ?, empfaenger = ?, summe_netto = ?, "
        "summe_ust = ?, summe_gesamt = ?, geaendert_am = ? WHERE id = ?",
        (kunde["id"], ap["id"] if ap else None, e.kopf["datum"], e.kopf["gueltig_bis"], e.kopf["betreff"],
         e.kopf["anmerkungen"], preisausweis(einstellungen), int(klein), empfaenger_text(kunde, ap),
         zahlen.speicherform(s.netto), zahlen.speicherform(s.ust), zahlen.speicherform(s.gesamt),
         protokoll.jetzt(), angebot_id),
    )
    pos.schreiben(con, "angebot_positionen", "angebot_id", angebot_id, e.positionen)
    return s


def anlegen(con: sqlite3.Connection, form) -> int:
    e = eingabe_pruefen(con, form)
    if e.fehler:
        raise ValidierungsFehler(e.fehler)
    einstellungen = stammdaten.laden(con)
    with con:
        nummer = naechste_nummer(con, date.fromisoformat(e.kopf["datum"]).year)
        cur = con.execute(
            "INSERT INTO angebote (nummer, kunde_id, datum, gueltig_bis, erstellt_am, geaendert_am) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (nummer, int(e.kopf["kunde_id"]), e.kopf["datum"], e.kopf["gueltig_bis"],
             protokoll.jetzt(), protokoll.jetzt()),
        )
        angebot_id = cur.lastrowid
        hinweise = pos.freie_uebernehmen(con, e.positionen, f"Angebot {nummer}")
        s = _schreiben(con, angebot_id, e, einstellungen)
        details = {
            "felder": {KOPF_BEZEICHNUNG[k]: ["", v] for k, v in _kopf_anzeige(con, e.kopf).items() if v},
            "hinweise": [f"Nummer {nummer}", *pos.als_text(e.positionen),
                         f"Gesamtbetrag {zahlen.geld(s.gesamt)}", *hinweise],
        }
        protokoll.schreiben(con, BEREICH, angebot_id, "angelegt", details)
    return angebot_id


def aendern(con: sqlite3.Connection, angebot_id: int, form) -> bool:
    alt = laden(con, angebot_id)
    if alt["status"] != "entwurf":
        raise AngebotsFehler("Abgeschlossene Angebote können nicht geändert werden. Bitte eine Kopie anlegen.")
    e = eingabe_pruefen(con, form)
    if e.fehler:
        raise ValidierungsFehler(e.fehler)
    einstellungen = stammdaten.laden(con)
    alte_pos = [dict(p) for p in positionen(con, angebot_id)]
    alt_kopf = {k: alt[k] for k in ("kunde_id", "ansprechpartner_id", "datum", "gueltig_bis", "betreff", "anmerkungen")}
    alt_kopf["ansprechpartner_id"] = str(alt_kopf["ansprechpartner_id"] or "")
    with con:
        hinweise = pos.freie_uebernehmen(con, e.positionen, f"Angebot {alt['nummer']}")
        s = _schreiben(con, angebot_id, e, einstellungen)
        a_alt, a_neu = _kopf_anzeige(con, alt_kopf), _kopf_anzeige(con, e.kopf)
        felder = {KOPF_BEZEICHNUNG[k]: [a_alt[k], a_neu[k]] for k in a_neu if a_alt[k] != a_neu[k]}
        pos_alt, pos_neu = pos.als_text(alte_pos), pos.als_text(e.positionen)
        if pos_alt != pos_neu:
            hinweise = [*(f"Entfernt: {t}" for t in pos_alt if t not in pos_neu),
                        *(f"Hinzugefügt: {t}" for t in pos_neu if t not in pos_alt), *hinweise]
            if not any(h.startswith(("Entfernt", "Hinzugefügt")) for h in hinweise):
                hinweise.insert(0, "Reihenfolge der Positionen geändert")
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
        protokoll.schreiben(con, BEREICH, angebot_id, "geaendert", details)
    return True


def vorgaben(con: sqlite3.Connection, kunde_id: int | None = None) -> dict:
    """Vorbelegung für ein neues Angebot."""
    einstellungen = stammdaten.laden(con)
    tag = heute()
    ap_id = ""
    if kunde_id:
        haupt = con.execute("SELECT id FROM ansprechpartner WHERE kunde_id = ? ORDER BY ist_hauptkontakt DESC, "
                            "position LIMIT 1", (kunde_id,)).fetchone()
        ap_id = str(haupt["id"]) if haupt else ""
    return {
        "kunde_id": str(kunde_id or ""), "ansprechpartner_id": ap_id, "datum": tag.isoformat(),
        "gueltig_bis": (tag + timedelta(days=einstellungen["angebot_gueltigkeit_tage"] or 30)).isoformat(),
        "betreff": "", "anmerkungen": "",
    }


# ---------------------------------------------------------------------------
# Abschließen, Status, Kopie, Löschen
# ---------------------------------------------------------------------------

def abschliessen(con: sqlite3.Connection, datenverzeichnis: Path, angebot_id: int) -> Path:
    """Friert das Angebot ein, erzeugt das PDF und legt es im Kundenordner ab."""
    from . import dokument  # spät importieren: reportlab nur bei Bedarf laden

    a = laden(con, angebot_id)
    if a["status"] != "entwurf":
        raise AngebotsFehler("Das Angebot ist bereits abgeschlossen.")
    if a["kunde_archiviert"]:
        raise AngebotsFehler("Der Kunde ist archiviert. Bitte zuerst reaktivieren.")
    einstellungen = stammdaten.laden(con)
    if not einstellungen["firma"].strip():
        raise AngebotsFehler("Bitte zuerst die eigenen Stammdaten (mindestens den Firmennamen) erfassen.")
    if not positionen(con, angebot_id):
        raise AngebotsFehler("Das Angebot enthält keine Positionen.")

    relativ = Path("kunden") / a["kundennummer"] / "angebote" / f"{a['nummer']}.pdf"
    ziel = Path(datenverzeichnis) / relativ
    ziel.parent.mkdir(parents=True, exist_ok=True)
    if ziel.exists():
        raise AngebotsFehler(f"Die Datei {relativ} existiert bereits.")
    try:
        with con:
            # Empfänger, Preisausweis und Summen ein letztes Mal aktualisieren
            kunde = kunden.laden(con, a["kunde_id"])
            ap = None
            if a["ansprechpartner_id"]:
                ap = con.execute("SELECT * FROM ansprechpartner WHERE id = ?", (a["ansprechpartner_id"],)).fetchone()
            klein = bool(einstellungen["kleinunternehmer"])
            s = berechnung.berechnen(positionen(con, angebot_id), klein, preisausweis(einstellungen))
            jetzt = protokoll.jetzt()
            con.execute(
                "UPDATE angebote SET empfaenger = ?, preisausweis = ?, kleinunternehmer = ?, summe_netto = ?, "
                "summe_ust = ?, summe_gesamt = ? WHERE id = ?",
                (empfaenger_text(kunde, ap), preisausweis(einstellungen), int(klein), zahlen.speicherform(s.netto),
                 zahlen.speicherform(s.ust), zahlen.speicherform(s.gesamt), angebot_id))
            daten = {**dict(laden(con, angebot_id)), "status": "offen"}
            ziel.write_bytes(dokument.erzeugen(daten, positionen(con, angebot_id), einstellungen, datenverzeichnis))
            con.execute("UPDATE angebote SET status = 'offen', pdf_datei = ?, abgeschlossen_am = ?, "
                        "geaendert_am = ? WHERE id = ?", (relativ.as_posix(), jetzt, jetzt, angebot_id))
            protokoll.schreiben(con, BEREICH, angebot_id, "abgeschlossen",
                                {"hinweise": [f"PDF abgelegt: {relativ.as_posix()}",
                                              f"Gesamtbetrag {zahlen.geld(s.gesamt)}"]})
    except Exception:
        ziel.unlink(missing_ok=True)
        raise
    return ziel


ERLAUBTE_UEBERGAENGE = {
    "offen": {"angenommen", "abgelehnt"},
    "angenommen": {"offen"},
    "abgelehnt": {"offen"},
}


def status_setzen(con: sqlite3.Connection, angebot_id: int, neu: str) -> None:
    a = laden(con, angebot_id)
    if neu not in ERLAUBTE_UEBERGAENGE.get(a["status"], set()):
        raise AngebotsFehler(f"Der Status kann nicht von „{STATUS[a['status']]}“ auf „{STATUS.get(neu, neu)}“ "
                             "geändert werden.")
    with con:
        con.execute("UPDATE angebote SET status = ?, geaendert_am = ? WHERE id = ?",
                    (neu, protokoll.jetzt(), angebot_id))
        protokoll.schreiben(con, BEREICH, angebot_id, "status",
                            {"felder": {"Status": [STATUS[a["status"]], STATUS[neu]]}})


def duplizieren(con: sqlite3.Connection, angebot_id: int) -> int:
    """Legt einen neuen Entwurf mit Kunde, Positionen und Texten des Angebots an."""
    a = laden(con, angebot_id)
    if a["kunde_archiviert"]:
        raise AngebotsFehler("Der Kunde ist archiviert. Bitte zuerst reaktivieren.")
    v = vorgaben(con, a["kunde_id"])
    form = {"kunde_id": str(a["kunde_id"]), "ansprechpartner_id": str(a["ansprechpartner_id"] or ""),
            "datum": v["datum"], "gueltig_bis": v["gueltig_bis"], "betreff": a["betreff"],
            "anmerkungen": a["anmerkungen"]}
    for i, p in enumerate(positionen(con, angebot_id)):
        for f in POSITIONSFELDER:
            form[f"pos-{i}-{f}"] = "" if p[f] is None else str(p[f])
    neu_id = anlegen(con, form)
    with con:
        protokoll.schreiben(con, BEREICH, neu_id, "kopie", {"hinweise": [f"Kopie von {a['nummer']}"]})
    return neu_id


def loeschen(con: sqlite3.Connection, angebot_id: int) -> sqlite3.Row:
    a = laden(con, angebot_id)
    if a["status"] != "entwurf":
        raise AngebotsFehler("Nur Entwürfe können gelöscht werden.")
    with con:
        con.execute("DELETE FROM angebote WHERE id = ?", (angebot_id,))
        protokoll.schreiben(con, BEREICH, angebot_id, "geloescht",
                            {"hinweise": [f"Entwurf {a['nummer']} für {a['kunde_firma']}"]})
    return a


def pdf_pfad(datenverzeichnis: Path, angebot) -> Path | None:
    if not angebot["pdf_datei"]:
        return None
    pfad = (Path(datenverzeichnis) / angebot["pdf_datei"]).resolve()
    if Path(datenverzeichnis).resolve() not in pfad.parents:
        return None
    return pfad


# Gemeinsame Funktionen (für bestehende Aufrufer)
ansprechpartner_name = pos.ansprechpartner_name
kunden_auswahl = pos.kunden_auswahl
leistungen_auswahl = pos.leistungen_auswahl
positionen_aus_formular = pos.aus_formular
