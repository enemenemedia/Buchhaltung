"""Übernahme abgeschlossener Rechnungen aus dem Modul „Rechnungen“ in die Belegerfassung.

Jede abgeschlossene Rechnung wird genau einmal verbucht (Tabelle rechnung_verbuchung) – als
festgeschriebene Einnahme, denn die Rechnung selbst ist bereits unveränderbar. Danach wird bei
jedem Abgleich der Zahlungseingang aus „Rechnungen“ (bezahlt_am) übernommen. Das gilt auch in
der Testversion, denn dafür ist keine XRechnung (XML) nötig.

Regeln (Zufluss-/Abflussprinzip, § 11 EStG – es zählt, wann Geld fließt):

  Rechnung (380)    Einnahme über den Rechnungsbetrag; Zahlungsdatum = Zahlungseingang.
  Storno (381)      - Original war bezahlt: Erstattung = negative Einnahme, Zahlungsdatum =
                      Zahlung der Erstattung.
                    - Original war offen: kein Geldfluss („verrechnet“). Der Beleg des Originals
                      wird storniert (Gegenbuchung ohne Zahlungsdatum); beide heben sich auf.
  Korrektur (384)   - Auf das Original wurde nichts gezahlt: Beleg des Originals stornieren,
                      Korrektur voll als Einnahme buchen.
                    - Auf das Original wurde gezahlt: nur die Differenz (Korrektur − Original)
                      buchen; Zahlungsdatum = Zahlung der Differenz.

Wird in „Rechnungen“ eine Zahlung zurückgesetzt oder ihr Datum geändert, kann der
festgeschriebene Beleg nicht geändert werden (GoBD): Er wird storniert und mit dem aktuellen
Stand neu gebucht.
"""

from __future__ import annotations

from decimal import Decimal

from .. import protokoll, zahlen
from . import service

GRUND_ZAHLUNG = "Zahlungseingang in „Rechnungen“ geändert bzw. zurückgesetzt"


def _cent(text) -> int:
    return service.cent(zahlen.parsen(text or "0"))


def _ust_satz(con, rechnung_id: int) -> str:
    saetze = {Decimal(z[0]).normalize() for z in con.execute(
        "SELECT DISTINCT ust_satz FROM rechnung_positionen WHERE rechnung_id = ?", (rechnung_id,))}
    if len(saetze) == 1:
        s = format(saetze.pop(), "f")
        return s if s in ("19", "7", "0") else "mehrere"
    return "mehrere" if saetze else "0"


def _kategorie(r) -> str:
    if r["kleinunternehmer"]:
        return "umsatz_kleinunternehmer"
    return "umsatz_steuerfrei" if _cent(r["summe_ust"]) == 0 else "umsatz_steuerpflichtig"


def _beschreibung(r, prefix: str | None = None) -> str:
    art = {"rechnung": "Rechnung", "storno": "Stornorechnung", "korrektur": "Rechnungskorrektur"}[r["art"]]
    text = f"{prefix or art} {r['nummer']}"
    if r["bezug_nummer"]:
        text += f" zu {r['bezug_nummer']}"
    if r["betreff"]:
        text += f": {r['betreff']}"
    return text[:300]


def aktiver_beleg(con, rechnung_id: int):
    """Der aktuell gültige Beleg zu einer Rechnung (festgeschrieben, nicht storniert)."""
    return con.execute("SELECT * FROM belege WHERE rechnung_id = ? AND quelle = 'rechnung' "
                       "AND status = 'festgeschrieben' ORDER BY id DESC LIMIT 1", (rechnung_id,)).fetchone()


def _rechnungen(con) -> list:
    return con.execute("""
        SELECT r.*, k.firma AS kunde_firma, o.nummer AS bezug_nummer, o.bezahlt_am AS bezug_bezahlt_am,
               o.summe_netto AS bezug_netto, o.summe_ust AS bezug_ust, o.summe_gesamt AS bezug_gesamt,
               v.art AS verbucht
        FROM rechnungen r
        JOIN kunden k ON k.id = r.kunde_id
        LEFT JOIN rechnungen o ON o.id = r.bezug_id
        LEFT JOIN rechnung_verbuchung v ON v.rechnung_id = r.id
        WHERE r.nummer IS NOT NULL AND r.status IN ('offen', 'bezahlt')
        ORDER BY r.abgeschlossen_am, r.id""").fetchall()


def _buchen(con, r, *, netto: int, ust: int, brutto: int, zahlung: str | None, beschreibung: str,
            notiz: str) -> int:
    werte = {"art": "einnahme", "belegdatum": r["rechnungsdatum"], "zahlungsdatum": zahlung,
             "partner": r["kunde_firma"], "rechnungsnummer": r["nummer"], "beschreibung": beschreibung,
             "kategorie": _kategorie(r), "ust_satz": _ust_satz(con, r["id"]),
             "netto_cent": netto, "ust_cent": ust, "brutto_cent": brutto,
             "zahlungsart": None, "notiz": notiz}
    bid = service._einfuegen(con, werte, quelle="rechnung", rechnung_id=r["id"])
    protokoll.schreiben(con, service.BEREICH, bid, "angelegt",
                        {"hinweise": [f"Automatisch aus Rechnung {r['nummer']} übernommen"]})
    service.festschreiben(con, bid)
    return bid


def _verbucht(con, rechnung_id: int, art: str) -> None:
    con.execute("INSERT INTO rechnung_verbuchung (rechnung_id, art) VALUES (?, ?)", (rechnung_id, art))


def _verbuchen(con, r) -> int | None:
    netto, ust, brutto = _cent(r["summe_netto"]), _cent(r["summe_ust"]), _cent(r["summe_gesamt"])
    bezahlt = r["bezahlt_am"]

    if r["art"] == "storno":
        original = aktiver_beleg(con, r["bezug_id"])
        if not r["bezug_bezahlt_am"]:
            # Kein Geldfluss: Original wird durch die Stornorechnung erledigt
            if original:
                service.stornieren(con, original["id"], f"Stornorechnung {r['nummer']}", rechnung_id=r["id"])
            _verbucht(con, r["id"], "verrechnet")
            return None
        bid = _buchen(con, r, netto=netto, ust=ust, brutto=brutto, zahlung=bezahlt,
                      beschreibung=_beschreibung(r, "Erstattung lt. Stornorechnung"),
                      notiz="Stornorechnung zu einer bezahlten Rechnung – Erstattung an den Kunden.")
        _verbucht(con, r["id"], "beleg")
        return bid

    if r["art"] == "korrektur":
        vorausbezahlt = _cent(r["vorausbezahlt"])
        if vorausbezahlt == 0:
            original = aktiver_beleg(con, r["bezug_id"])
            if original:
                service.stornieren(con, original["id"], f"Ersetzt durch Rechnungskorrektur {r['nummer']}")
            bid = _buchen(con, r, netto=netto, ust=ust, brutto=brutto, zahlung=bezahlt,
                          beschreibung=_beschreibung(r), notiz="Rechnungskorrektur – ersetzt die Originalrechnung.")
            _verbucht(con, r["id"], "beleg")
            return bid
        d_netto = netto - _cent(r["bezug_netto"])
        d_ust = ust - _cent(r["bezug_ust"])
        d_brutto = brutto - _cent(r["bezug_gesamt"])
        if d_netto == d_ust == d_brutto == 0:
            _verbucht(con, r["id"], "ohne_beleg")
            return None
        bid = _buchen(con, r, netto=d_netto, ust=d_ust, brutto=d_brutto, zahlung=bezahlt,
                      beschreibung=_beschreibung(r, "Differenz lt. Rechnungskorrektur"),
                      notiz=f"Original bereits bezahlt ({zahlen.geld(service.als_euro(vorausbezahlt))}); "
                            "gebucht wird nur die Differenz.")
        _verbucht(con, r["id"], "beleg")
        return bid

    bid = _buchen(con, r, netto=netto, ust=ust, brutto=brutto, zahlung=bezahlt,
                  beschreibung=_beschreibung(r), notiz="Automatisch aus dem Modul „Rechnungen“ übernommen.")
    _verbucht(con, r["id"], "beleg")
    return bid


def _zahlung_abgleichen(con, r) -> int | None:
    b = aktiver_beleg(con, r["id"])
    if b is None or b["zahlungsdatum"] == r["bezahlt_am"]:
        return None
    if b["zahlungsdatum"] is None:
        service.zahlung_erfassen(con, b["id"], r["bezahlt_am"])
        return b["id"]
    # Zahlung zurückgesetzt oder Datum geändert: stornieren und neu buchen (GoBD)
    service.stornieren(con, b["id"], GRUND_ZAHLUNG)
    werte = {k: b[k] for k in service.INHALT}
    werte["zahlungsdatum"] = r["bezahlt_am"]
    neu = service._einfuegen(con, werte, quelle="rechnung", rechnung_id=r["id"])
    protokoll.schreiben(con, service.BEREICH, neu, "angelegt",
                        {"hinweise": [f"Neu gebucht nach Änderung der Zahlung zu Rechnung {r['nummer']}"]})
    service.festschreiben(con, neu)
    return neu


def abgleichen(con) -> dict:
    """Übernimmt neue Rechnungen und Zahlungen. -> {"neu": [...], "zahlungen": [...]} (Beleg-IDs)"""
    erg = {"neu": [], "zahlungen": []}
    for r in _rechnungen(con):
        if r["verbucht"] is None:
            bid = _verbuchen(con, r)
            if bid:
                erg["neu"].append(bid)
        elif r["verbucht"] == "beleg":
            bid = _zahlung_abgleichen(con, r)
            if bid:
                erg["zahlungen"].append(bid)
    if erg["neu"] or erg["zahlungen"]:
        con.commit()
    return erg
