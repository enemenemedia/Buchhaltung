"""Belegerfassung: Einnahmen und Ausgaben mit Belegdatei.

Ablauf (GoBD):
  entwurf          frei änderbar und löschbar
  festgeschrieben  fortlaufende Belegnummer, unveränderbar (auch per Datenbank-Trigger);
                   nur das Zahlungsdatum darf einmal nachgetragen werden
  storniert        durch eine Gegenbuchung mit umgekehrten Beträgen (Belegart „storno“)

Beträge werden in ganzen Cent gespeichert. Belegdateien liegen unveränderlich unter ihrer
SHA-256-Prüfsumme in <Datenordner>/belege/<Jahr>/.
"""

from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from .. import protokoll, zahlen
from . import kategorien
from .xrechnung_import import XRechnungFehler, ist_eigene_rechnung, lese_xrechnung

BEREICH = "beleg"
ABLAGE = "belege"
ERSTES_JAHR = 2024                 # Jahresauswahl beginnt mit 2024
ERLAUBTE_ENDUNGEN = {".pdf", ".jpg", ".jpeg", ".png", ".xml"}
DATEI_MAX = 20 * 1024 * 1024
ZAHLUNGSARTEN = ["Überweisung", "Lastschrift", "Bar", "EC-/Kreditkarte", "PayPal", "Verrechnung"]
STATUS = {"entwurf": "Entwurf", "festgeschrieben": "Festgeschrieben", "storniert": "Storniert"}
UST_SAETZE = {"19": Decimal("0.19"), "7": Decimal("0.07"), "0": Decimal("0")}
UST_TEXT = {"19": "19 %", "7": "7 %", "0": "0 % / steuerfrei", "mehrere": "mehrere Steuersätze"}

INHALT = ["art", "belegdatum", "zahlungsdatum", "partner", "rechnungsnummer", "beschreibung", "kategorie",
          "ust_satz", "netto_cent", "ust_cent", "brutto_cent", "zahlungsart", "notiz", "nutzungsdauer"]
BEZEICHNUNGEN = {"art": "Art", "belegdatum": "Belegdatum", "zahlungsdatum": "Bezahlt am", "partner": "Partner",
                 "rechnungsnummer": "Rechnungsnummer", "beschreibung": "Beschreibung", "kategorie": "Kategorie",
                 "ust_satz": "USt-Satz", "netto_cent": "Netto (Cent)", "ust_cent": "USt (Cent)",
                 "brutto_cent": "Brutto (Cent)", "zahlungsart": "Zahlungsart", "notiz": "Notiz",
                 "nutzungsdauer": "Nutzungsdauer (Jahre)"}


class BelegFehler(Exception):
    pass


class ValidierungsFehler(Exception):
    def __init__(self, fehler: dict[str, str], werte: dict):
        super().__init__("; ".join(fehler.values()))
        self.fehler, self.werte = fehler, werte


# --------------------------------------------------------------------------- Beträge

def cent(wert: Decimal) -> int:
    return int((Decimal(wert) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def eingabe_cent(text, feld: str) -> int:
    return cent(zahlen.parsen(text, feld=feld))


def ust_aus_netto(netto: int, satz: str) -> int:
    return cent(Decimal(netto) / 100 * UST_SAETZE[satz])


def netto_aus_brutto(brutto: int, satz: str) -> int:
    return cent(Decimal(brutto) / 100 / (1 + UST_SAETZE[satz]))


def als_euro(c) -> Decimal:
    return Decimal(int(c or 0)) / 100


def als_eingabe(c) -> str:
    """12345 -> '123,45' (für Formularfelder)"""
    if c is None or c == "":
        return ""
    return zahlen.geld(als_euro(c), mit_waehrung=False).replace(".", "")


def _gueltiges_datum(text) -> bool:
    try:
        date.fromisoformat(text)
        return True
    except (TypeError, ValueError):
        return False


# --------------------------------------------------------------------------- Formular

def formular_auswerten(form) -> dict:
    """Liest und prüft das Belegformular. Liefert die Werte oder wirft ValidierungsFehler."""
    f: dict[str, str] = {}
    w = {k: (form.get(k) or "").strip() for k in
         ("art", "belegdatum", "zahlungsdatum", "partner", "rechnungsnummer", "beschreibung", "kategorie",
          "ust_satz", "netto", "ust", "brutto", "zahlungsart", "notiz", "nutzungsdauer")}

    if w["art"] not in ("einnahme", "ausgabe"):
        f["art"] = "Bitte Einnahme oder Ausgabe wählen."
    if not _gueltiges_datum(w["belegdatum"]):
        f["belegdatum"] = "Belegdatum fehlt oder ist ungültig."
    if w["zahlungsdatum"] and not _gueltiges_datum(w["zahlungsdatum"]):
        f["zahlungsdatum"] = "Zahlungsdatum ist ungültig."
    if not w["partner"]:
        f["partner"] = "Bitte Kunde bzw. Lieferant angeben."
    if not w["beschreibung"]:
        f["beschreibung"] = "Bitte eine Beschreibung angeben."
    if w["kategorie"] not in kategorien.fuer_art(w["art"]):
        f["kategorie"] = "Die Kategorie passt nicht zur Belegart."
    satz = w["ust_satz"]
    if satz not in UST_TEXT:
        f["ust_satz"] = "Bitte einen USt-Satz wählen."
    if w["kategorie"] in kategorien.OHNE_UST:
        satz = w["ust_satz"] = "0"

    # Beträge: aus Netto, aus Brutto oder vollständig vorgegeben
    netto = brutto = ust = None
    try:
        netto = eingabe_cent(w["netto"], "Netto") if w["netto"] else None
        brutto = eingabe_cent(w["brutto"], "Brutto") if w["brutto"] else None
        ust = eingabe_cent(w["ust"], "USt") if w["ust"] else None
        fest = satz in UST_SAETZE
        if netto is not None and brutto is not None:
            ust = brutto - netto
        elif netto is not None and ust is not None:
            brutto = netto + ust
        elif brutto is not None and ust is not None:
            netto = brutto - ust
        elif netto is not None and fest:
            ust = ust_aus_netto(netto, satz)
            brutto = netto + ust
        elif brutto is not None and fest:
            netto = netto_aus_brutto(brutto, satz)
            ust = brutto - netto
        else:
            f["netto"] = "Bitte Netto- oder Bruttobetrag angeben (bei mehreren Steuersätzen Netto und Brutto)."
        if netto is not None and ust is not None:
            if satz == "0" and ust != 0:
                f["ust"] = "Bei 0 % USt muss der Steuerbetrag 0 sein."
            elif satz in ("19", "7") and abs(ust - ust_aus_netto(netto, satz)) > 5:
                f["ust"] = "Der USt-Betrag passt nicht zum gewählten Steuersatz (Abweichung über 0,05 €)."
    except zahlen.ZahlFehler as e:
        f["netto"] = str(e)

    nd = None
    if w["kategorie"] == "anlagevermoegen":
        try:
            nd = int(w["nutzungsdauer"])
            if not 1 <= nd <= 50:
                raise ValueError
        except ValueError:
            f["nutzungsdauer"] = "Für Anlagevermögen bitte die Nutzungsdauer in Jahren angeben (AfA-Tabelle)."
    if w["kategorie"] == "gwg" and netto is not None and abs(netto) > kategorien.GWG_GRENZE_CENT:
        f["kategorie"] = "GWG nur bis 800 € netto – darüber bitte „Anlagevermögen“ mit Nutzungsdauer wählen."

    werte = {"art": w["art"], "belegdatum": w["belegdatum"], "zahlungsdatum": w["zahlungsdatum"] or None,
             "partner": w["partner"], "rechnungsnummer": w["rechnungsnummer"] or None,
             "beschreibung": w["beschreibung"][:300], "kategorie": w["kategorie"], "ust_satz": satz,
             "netto_cent": netto, "ust_cent": ust, "brutto_cent": brutto,
             "zahlungsart": w["zahlungsart"] or None, "notiz": w["notiz"] or None, "nutzungsdauer": nd}
    if f:
        raise ValidierungsFehler(f, {**werte, "netto": w["netto"], "ust": w["ust"], "brutto": w["brutto"],
                                     "nutzungsdauer": w["nutzungsdauer"]})
    return werte


# --------------------------------------------------------------------------- Dateien

def datei_ablegen(daten: Path, inhalt: bytes, dateiname: str) -> tuple[str, str, str]:
    """Speichert eine Belegdatei unveränderlich unter ihrer Prüfsumme. -> (Name, relativer Pfad, SHA-256)"""
    endung = Path(dateiname).suffix.lower()
    if endung not in ERLAUBTE_ENDUNGEN:
        raise BelegFehler(f"Dateityp {endung or '?'} nicht erlaubt (erlaubt: PDF, JPG, PNG, XML).")
    if not inhalt:
        raise BelegFehler("Die Datei ist leer.")
    if len(inhalt) > DATEI_MAX:
        raise BelegFehler("Die Datei ist zu groß (höchstens 20 MB).")
    sha = hashlib.sha256(inhalt).hexdigest()
    relativ = Path(str(date.today().year)) / sha[:2] / f"{sha}{endung}"
    ziel = Path(daten) / ABLAGE / relativ
    if not ziel.exists():
        ziel.parent.mkdir(parents=True, exist_ok=True)
        ziel.write_bytes(inhalt)
    return Path(dateiname).name[:200], relativ.as_posix(), sha


def datei_pfad(daten: Path, beleg) -> Path | None:
    if not beleg["datei_pfad"]:
        return None
    ablage = (Path(daten) / ABLAGE).resolve()
    pfad = (ablage / beleg["datei_pfad"]).resolve()
    return pfad if ablage in pfad.parents and pfad.is_file() else None


# --------------------------------------------------------------------------- Lesen

def laden(con, beleg_id: int):
    b = con.execute("SELECT * FROM belege WHERE id = ?", (beleg_id,)).fetchone()
    if b is None:
        raise BelegFehler("Beleg nicht gefunden.")
    return b


@dataclass
class Filter:
    jahr: str = ""
    art: str = ""
    status: str = ""
    suche: str = ""
    tag: str = ""                  # Tag-ID; "ohne" = Belege ohne Tag


def liste(con, flt: Filter) -> list:
    sql, p = "SELECT * FROM belege WHERE 1=1", []
    if flt.jahr:
        sql += " AND (substr(belegdatum, 1, 4) = ? OR substr(zahlungsdatum, 1, 4) = ?)"
        p += [flt.jahr, flt.jahr]
    if flt.art in ("einnahme", "ausgabe"):
        sql += " AND art = ?"
        p.append(flt.art)
    if flt.status in STATUS:
        sql += " AND status = ?"
        p.append(flt.status)
    elif flt.status == "offen":
        sql += " AND status = 'festgeschrieben' AND quelle <> 'storno' AND zahlungsdatum IS NULL"
    if flt.suche:
        sql += (" AND (partner LIKE ? OR beschreibung LIKE ? OR rechnungsnummer LIKE ? OR belegnummer LIKE ?)")
        p += [f"%{flt.suche}%"] * 4
    if flt.tag == "ohne":
        sql += " AND NOT EXISTS (SELECT 1 FROM beleg_tags bt WHERE bt.beleg_id = belege.id)"
    elif flt.tag.isdigit():
        sql += " AND EXISTS (SELECT 1 FROM beleg_tags bt WHERE bt.beleg_id = belege.id AND bt.tag_id = ?)"
        p.append(int(flt.tag))
    sql += " ORDER BY belegdatum DESC, id DESC"
    return con.execute(sql, p).fetchall()


def kennzahlen(belege: list, jahr: str) -> dict:
    """Zahlungen im gewählten Jahr – auch Entwürfe (Auswertung ohne Festschreibung möglich)."""
    def im_jahr(b):
        return bool(b["zahlungsdatum"]) and (not jahr or b["zahlungsdatum"][:4] == jahr)
    ein = sum(b["brutto_cent"] for b in belege if b["art"] == "einnahme" and im_jahr(b))
    aus = sum(b["brutto_cent"] for b in belege if b["art"] == "ausgabe" and im_jahr(b))
    return {"einnahmen": ein, "ausgaben": aus, "saldo": ein - aus,
            "entwuerfe": sum(1 for b in belege if b["status"] == "entwurf"),
            "offen": sum(1 for b in belege if b["status"] == "festgeschrieben" and b["quelle"] != "storno"
                         and not b["zahlungsdatum"])}


def jahre(con) -> list[str]:
    """Alle Jahre ab 2024 bis heute, dazu ältere/spätere Jahre, in denen es Belege gibt."""
    js = {r[0] for r in con.execute("SELECT DISTINCT substr(belegdatum, 1, 4) FROM belege")}
    js |= {r[0] for r in con.execute("SELECT DISTINCT substr(zahlungsdatum, 1, 4) FROM belege WHERE zahlungsdatum IS NOT NULL")}
    js |= {str(j) for j in range(ERSTES_JAHR, max(date.today().year, ERSTES_JAHR) + 1)}
    return sorted(js, reverse=True)


def standard_jahr(con) -> str:
    """Vorauswahl: das neueste Jahr mit Belegen, sonst 2024 (Beginn der Buchhaltung)."""
    z = con.execute("SELECT MAX(substr(belegdatum, 1, 4)) FROM belege").fetchone()[0]
    return z or str(ERSTES_JAHR)


# --------------------------------------------------------------------------- Schreiben

def _einfuegen(con, werte: dict, *, quelle="manuell", datei=None, rechnung_id=None, storno_von=None) -> int:
    felder = INHALT + ["datei_name", "datei_pfad", "datei_sha256", "quelle", "rechnung_id", "storno_von"]
    werte = {**{k: None for k in INHALT}, **werte}
    cur = con.execute(
        f"INSERT INTO belege ({', '.join(felder)}) VALUES ({', '.join('?' * len(felder))})",
        [werte[k] for k in INHALT] + list(datei or (None, None, None)) + [quelle, rechnung_id, storno_von])
    return cur.lastrowid


def anlegen(con, daten: Path, werte: dict, datei: tuple[bytes, str] | None = None, *, festschreiben_sofort=False) -> int:
    abgelegt = datei_ablegen(daten, *datei) if datei else None
    bid = _einfuegen(con, werte, datei=abgelegt)
    protokoll.schreiben(con, BEREICH, bid, "angelegt",
                        {"hinweise": [f"Datei: {abgelegt[0]}"] if abgelegt else []})
    if festschreiben_sofort:
        festschreiben(con, bid)
    return bid


def aendern(con, daten: Path, beleg_id: int, werte: dict, datei: tuple[bytes, str] | None = None) -> None:
    alt = laden(con, beleg_id)
    if alt["status"] != "entwurf":
        raise BelegFehler("Festgeschriebene Belege können nicht geändert werden. Bitte stornieren und neu erfassen.")
    diff = protokoll.felder_diff(alt, werte, BEZEICHNUNGEN)
    con.execute(f"UPDATE belege SET {', '.join(f'{k} = ?' for k in INHALT)}, geaendert_am = ? WHERE id = ?",
                [werte[k] for k in INHALT] + [protokoll.jetzt(), beleg_id])
    if datei:
        name, rel, sha = datei_ablegen(daten, *datei)
        con.execute("UPDATE belege SET datei_name = ?, datei_pfad = ?, datei_sha256 = ? WHERE id = ?",
                    (name, rel, sha, beleg_id))
        diff["Datei"] = [alt["datei_name"], name]
    if diff:
        protokoll.schreiben(con, BEREICH, beleg_id, "geaendert", {"felder": diff})


def naechste_belegnummer(con, art: str, datum: str) -> str:
    praefix = f"{'E' if art == 'einnahme' else 'A'}-{datum[:4]}-"
    z = con.execute("SELECT MAX(CAST(substr(belegnummer, ?) AS INTEGER)) FROM belege WHERE belegnummer LIKE ?",
                    (len(praefix) + 1, praefix + "%")).fetchone()
    return f"{praefix}{(z[0] or 0) + 1:04d}"


def festschreiben(con, beleg_id: int) -> str:
    b = laden(con, beleg_id)
    if b["status"] != "entwurf":
        raise BelegFehler("Der Beleg ist bereits festgeschrieben.")
    if b["kategorie"] == "anlagevermoegen" and not b["nutzungsdauer"]:
        raise BelegFehler("Für Anlagevermögen fehlt die Nutzungsdauer.")
    nummer = naechste_belegnummer(con, b["art"], b["belegdatum"])
    jetzt = protokoll.jetzt()
    con.execute("UPDATE belege SET status = 'festgeschrieben', belegnummer = ?, festgeschrieben_am = ?, "
                "geaendert_am = ? WHERE id = ?", (nummer, jetzt, jetzt, beleg_id))
    protokoll.schreiben(con, BEREICH, beleg_id, "festgeschrieben",
                        {"hinweise": [f"Belegnummer {nummer}"] + ([f"SHA-256 {b['datei_sha256']}"] if b["datei_sha256"] else [])})
    return nummer


def loeschen(con, beleg_id: int) -> None:
    b = laden(con, beleg_id)
    if b["status"] != "entwurf":
        raise BelegFehler("Nur Entwürfe können gelöscht werden.")
    protokoll.schreiben(con, BEREICH, beleg_id, "geloescht",
                        {"hinweise": [f"{b['partner']}: {b['beschreibung']} ({zahlen.geld(als_euro(b['brutto_cent']))})"]})
    con.execute("DELETE FROM belege WHERE id = ?", (beleg_id,))


def zahlung_erfassen(con, beleg_id: int, datum: str, zahlungsart: str | None = None) -> None:
    b = laden(con, beleg_id)
    if b["status"] == "storniert" or b["quelle"] == "storno":
        raise BelegFehler("Für stornierte Belege kann keine Zahlung erfasst werden.")
    if b["zahlungsdatum"]:
        raise BelegFehler("Das Zahlungsdatum ist bereits erfasst.")
    if not _gueltiges_datum(datum):
        raise BelegFehler("Bitte ein gültiges Zahlungsdatum angeben.")
    con.execute("UPDATE belege SET zahlungsdatum = ?, zahlungsart = COALESCE(?, zahlungsart), geaendert_am = ? "
                "WHERE id = ?", (datum, zahlungsart or None, protokoll.jetzt(), beleg_id))
    protokoll.schreiben(con, BEREICH, beleg_id, "zahlung",
                        {"felder": {"Bezahlt am": [None, datum]} | ({"Zahlungsart": [None, zahlungsart]} if zahlungsart else {})})


def stornieren(con, beleg_id: int, grund: str, *, rechnung_id: int | None = None) -> int:
    """Gegenbuchung mit umgekehrten Beträgen; das Zahlungsdatum des Originals wird übernommen,
    damit sich beide in der EÜR genau aufheben."""
    b = laden(con, beleg_id)
    grund = (grund or "").strip()
    if b["status"] != "festgeschrieben" or b["quelle"] == "storno":
        raise BelegFehler("Nur festgeschriebene Belege können storniert werden.")
    if not grund:
        raise BelegFehler("Bitte einen Stornogrund angeben.")
    werte = {k: b[k] for k in INHALT}
    werte.update(belegdatum=date.today().isoformat(), netto_cent=-b["netto_cent"], ust_cent=-b["ust_cent"],
                 brutto_cent=-b["brutto_cent"], notiz=grund[:500],
                 beschreibung=f"Storno zu {b['belegnummer']}: {b['beschreibung']}"[:300])
    sid = _einfuegen(con, werte, quelle="storno", storno_von=b["id"], rechnung_id=rechnung_id)
    from .tags import kopieren
    kopieren(con, b["id"], sid)          # die Gegenbuchung trägt dieselben Tags
    protokoll.schreiben(con, BEREICH, sid, "angelegt", {"hinweise": [f"Storno zu {b['belegnummer']}: {grund}"]})
    festschreiben(con, sid)
    con.execute("UPDATE belege SET status = 'storniert' WHERE id = ?", (b["id"],))
    protokoll.schreiben(con, BEREICH, b["id"], "storniert",
                        {"hinweise": [f"Grund: {grund}", f"Gegenbuchung {laden(con, sid)['belegnummer']}"]})
    return sid


# --------------------------------------------------------------------------- XRechnung-Import

def xrechnung_importieren(con, daten: Path, inhalt: bytes, dateiname: str) -> tuple[int, list[str]]:
    """Legt aus einer XRechnung (meist Eingangsrechnung) einen Entwurf an. -> (Beleg-ID, Hinweise)"""
    try:
        x = lese_xrechnung(inhalt)
    except XRechnungFehler as e:
        raise BelegFehler(str(e)) from None
    s = con.execute("SELECT firma, ust_id FROM stammdaten LIMIT 1").fetchone()
    eigen = ist_eigene_rechnung(x, s["firma"] if s else "", s["ust_id"] if s else "")
    art = "einnahme" if eigen else "ausgabe"
    partner = (x.kaeufer if eigen else x.verkaeufer) or "(unbekannt)"
    vorhanden = con.execute("SELECT id FROM belege WHERE partner = ? AND rechnungsnummer = ? AND quelle <> 'storno'",
                            (partner, x.rechnungsnummer)).fetchone()
    if vorhanden:
        raise BelegFehler(f"Rechnung {x.rechnungsnummer} von {partner} ist bereits erfasst.")
    hinweise = []
    if x.waehrung != "EUR":
        hinweise.append(f"Rechnungswährung ist {x.waehrung} – Beträge bitte in Euro umrechnen.")
    if eigen:
        hinweise.append("Das ist eine eigene Ausgangsrechnung. Rechnungen aus dem Modul „Rechnungen“ "
                        "werden automatisch übernommen und müssen nicht importiert werden.")
    vz = -1 if x.gutschrift else 1
    notiz = f"Importiert aus XRechnung ({x.format})"
    if x.bezug:
        notiz += f", Bezug auf Rechnung {x.bezug}"
    if x.faellig_am:
        notiz += f", fällig am {x.faellig_am}"
    werte = {"art": art, "belegdatum": x.rechnungsdatum or date.today().isoformat(), "partner": partner,
             "rechnungsnummer": x.rechnungsnummer or None, "beschreibung": x.beschreibung,
             "kategorie": kategorien.STANDARD[art], "ust_satz": x.ust_satz,
             "netto_cent": vz * x.netto_cent, "ust_cent": vz * x.ust_cent, "brutto_cent": vz * x.brutto_cent,
             "notiz": notiz}
    abgelegt = datei_ablegen(daten, inhalt, dateiname if dateiname.lower().endswith(".xml") else dateiname + ".xml")
    bid = _einfuegen(con, werte, quelle="xrechnung", datei=abgelegt)
    protokoll.schreiben(con, BEREICH, bid, "angelegt", {"hinweise": [f"XRechnung {x.format}: {abgelegt[0]}"]})
    return bid, hinweise


# --------------------------------------------------------------------------- Export

def csv_export(belege: list, tags_je_beleg: dict[int, list[str]] | None = None) -> str:
    tags_je_beleg = tags_je_beleg or {}
    puffer = io.StringIO()
    w = csv.writer(puffer, delimiter=";", lineterminator="\r\n")
    w.writerow(["Belegnummer", "Status", "Art", "Belegdatum", "Bezahlt am", "Partner", "Rechnungsnummer",
                "Beschreibung", "Kategorie", "Tags", "USt-Satz", "Netto", "USt", "Brutto", "Zahlungsart", "Quelle",
                "Datei", "SHA-256"])
    for b in belege:
        w.writerow([b["belegnummer"] or "", STATUS[b["status"]], b["art"], b["belegdatum"], b["zahlungsdatum"] or "",
                    b["partner"], b["rechnungsnummer"] or "", b["beschreibung"], kategorien.bezeichnung(b["kategorie"]),
                    ", ".join(tags_je_beleg.get(b["id"], [])),
                    b["ust_satz"], als_eingabe(b["netto_cent"]), als_eingabe(b["ust_cent"]),
                    als_eingabe(b["brutto_cent"]), b["zahlungsart"] or "", b["quelle"], b["datei_name"] or "",
                    b["datei_sha256"] or ""])
    return "﻿" + puffer.getvalue()


def fuer_formular(b) -> dict:
    d = dict(b)
    d.update(netto=als_eingabe(b["netto_cent"]), ust=als_eingabe(b["ust_cent"]), brutto=als_eingabe(b["brutto_cent"]))
    return d


def protokoll_lesen(con, beleg_id: int) -> list[dict]:
    return protokoll.lesen(con, BEREICH, beleg_id)

