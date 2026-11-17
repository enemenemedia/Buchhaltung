"""Belegerfassung: Erfassen, Festschreiben (GoBD), Storno, Zahlung, Import, Export,
und die automatische Übernahme abgeschlossener Rechnungen."""

import io
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from app import create_app, db
from app.belege import abgleich, service
from app.belege.xrechnung_import import XRechnungFehler, lese_xrechnung
from app.kunden import service as kunden
from app.rechnungen import service as rechnungen
from app.stammdaten import service as stammdaten
from tests.test_rechnungen import STAMMDATEN
from tests.test_storno_korrektur import formular

BEISPIELE = Path(__file__).parent / "beispiele"
JAHR = date.today().year
HEUTE = date.today().isoformat()

BELEG = {"art": "ausgabe", "belegdatum": f"{JAHR}-03-05", "partner": "Bürobedarf Meyer",
         "beschreibung": "Druckerpapier", "kategorie": "buero", "ust_satz": "19", "netto": "100,00"}


@pytest.fixture
def app(tmp_path):
    return create_app(tmp_path)


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def con(app):
    c = db.verbinden(app.config["DATENBANK"])
    yield c
    c.close()


def belege(con):
    return con.execute("SELECT * FROM belege ORDER BY id").fetchall()


# ------------------------------------------------------------------ Formular und Beträge

@pytest.mark.parametrize("eingabe,netto,ust,brutto", [
    ({"netto": "100,00"}, 10000, 1900, 11900),
    ({"netto": "", "brutto": "59,50"}, 5000, 950, 5950),
    ({"netto": "1.234,56"}, 123456, 23457, 146913),
    ({"netto": "-19,99"}, -1999, -380, -2379),
    ({"ust_satz": "7", "netto": "10"}, 1000, 70, 1070),
    ({"ust_satz": "mehrere", "netto": "100", "brutto": "112,50"}, 10000, 1250, 11250),
])
def test_betraege(eingabe, netto, ust, brutto):
    w = service.formular_auswerten({**BELEG, **eingabe})
    assert (w["netto_cent"], w["ust_cent"], w["brutto_cent"]) == (netto, ust, brutto)


def test_validierung():
    with pytest.raises(service.ValidierungsFehler) as e:
        service.formular_auswerten({**BELEG, "partner": "", "kategorie": "umsatz_steuerpflichtig", "ust": "5"})
    assert set(e.value.fehler) >= {"partner", "kategorie"}
    with pytest.raises(service.ValidierungsFehler) as e:
        service.formular_auswerten({**BELEG, "kategorie": "anlagevermoegen", "netto": "2000"})
    assert "nutzungsdauer" in e.value.fehler
    with pytest.raises(service.ValidierungsFehler) as e:
        service.formular_auswerten({**BELEG, "kategorie": "gwg", "netto": "900"})
    assert "kategorie" in e.value.fehler
    # USt-Zahlung an das Finanzamt: immer ohne Vorsteuer
    w = service.formular_auswerten({**BELEG, "kategorie": "ust_zahlung", "netto": "500"})
    assert (w["ust_satz"], w["ust_cent"], w["brutto_cent"]) == ("0", 0, 50000)


# ------------------------------------------------------------------ Ablauf in der Oberfläche

def test_erfassen_mit_datei_und_festschreiben(client, app, con):
    r = client.post("/belege/neu", data={**BELEG, "aktion": "festschreiben",
                                         "datei": (io.BytesIO(b"%PDF-1.4 Quittung"), "quittung.pdf")},
                    content_type="multipart/form-data")
    assert r.status_code == 302
    (b,) = belege(con)
    assert (b["status"], b["belegnummer"], b["brutto_cent"]) == ("festgeschrieben", f"A-{JAHR}-0001", 11900)
    assert (app.config["DATENVERZEICHNIS"] / "belege" / b["datei_pfad"]).read_bytes() == b"%PDF-1.4 Quittung"
    assert client.get(f"/belege/{b['id']}/datei").data == b"%PDF-1.4 Quittung"
    html = client.get(f"/belege/{b['id']}").get_data(as_text=True)
    assert "Festgeschrieben" in html and b["datei_sha256"] in html


def test_dateityp_und_groesse(client, con):
    r = client.post("/belege/neu", data={**BELEG, "datei": (io.BytesIO(b"x"), "virus.exe")},
                    content_type="multipart/form-data")
    assert r.status_code == 422 and "nicht erlaubt" in r.get_data(as_text=True)
    assert belege(con) == []
    # 8 MB sind erlaubt (die allgemeine Grenze von 6 MB gilt hier nicht)
    r = client.post("/belege/neu", data={**BELEG, "datei": (io.BytesIO(b"0" * 8_000_000), "scan.pdf")},
                    content_type="multipart/form-data")
    assert r.status_code == 302


def test_belegnummern_fortlaufend_je_art(client, con):
    for _ in range(2):
        client.post("/belege/neu", data={**BELEG, "aktion": "festschreiben"})
    client.post("/belege/neu", data={**BELEG, "art": "einnahme", "kategorie": "umsatz_steuerpflichtig",
                                     "aktion": "festschreiben"})
    client.post("/belege/neu", data=BELEG)                      # Entwurf: noch ohne Nummer
    assert [b["belegnummer"] for b in belege(con)] == [f"A-{JAHR}-0001", f"A-{JAHR}-0002", f"E-{JAHR}-0001", None]


def test_festgeschrieben_unveraenderbar(client, con):
    client.post("/belege/neu", data={**BELEG, "aktion": "festschreiben"})
    client.post("/belege/1/bearbeiten", data={**BELEG, "netto": "1,00"})
    client.post("/belege/1/loeschen")
    assert belege(con)[0]["netto_cent"] == 10000
    with pytest.raises(sqlite3.IntegrityError, match="GoBD"):
        con.execute("UPDATE belege SET netto_cent = 1, brutto_cent = 1901 WHERE id = 1")
    with pytest.raises(sqlite3.IntegrityError, match="GoBD"):
        con.execute("DELETE FROM belege WHERE id = 1")


def test_zahlung_nur_einmal(client, con):
    client.post("/belege/neu", data={**BELEG, "aktion": "festschreiben"})
    client.post("/belege/1/zahlung", data={"zahlungsdatum": f"{JAHR}-03-20", "zahlungsart": "Überweisung"})
    client.post("/belege/1/zahlung", data={"zahlungsdatum": f"{JAHR}-04-01"})
    b = belege(con)[0]
    assert (b["zahlungsdatum"], b["zahlungsart"]) == (f"{JAHR}-03-20", "Überweisung")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("UPDATE belege SET zahlungsdatum = '2030-01-01' WHERE id = 1")


def test_storno(client, con):
    client.post("/belege/neu", data={**BELEG, "aktion": "festschreiben", "zahlungsdatum": f"{JAHR}-03-06"})
    client.post("/belege/1/stornieren", data={"grund": ""})
    assert belege(con)[0]["status"] == "festgeschrieben"
    client.post("/belege/1/stornieren", data={"grund": "Doppelt erfasst"})
    o, s = belege(con)
    assert o["status"] == "storniert" and s["quelle"] == "storno" and s["storno_von"] == 1
    assert s["brutto_cent"] == -11900 and s["zahlungsdatum"] == f"{JAHR}-03-06"
    assert s["belegnummer"] == f"A-{JAHR}-0002"
    client.post("/belege/2/stornieren", data={"grund": "x"})          # Storno des Stornos: nicht möglich
    assert len(belege(con)) == 2


def test_entwurf_bearbeiten_loeschen_protokoll(client, con):
    client.post("/belege/neu", data=BELEG)
    client.post("/belege/1/bearbeiten", data={**BELEG, "netto": "200"})
    assert belege(con)[0]["brutto_cent"] == 23800
    client.post("/belege/1/loeschen")
    assert belege(con) == []
    aktionen = [z["aktion"] for z in con.execute("SELECT aktion FROM aenderungsprotokoll WHERE bereich = 'beleg' ORDER BY id")]
    assert aktionen == ["angelegt", "geaendert", "geloescht"]


def test_liste_filter_export(client):
    client.post("/belege/neu", data={**BELEG, "aktion": "festschreiben", "zahlungsdatum": f"{JAHR}-03-06"})
    client.post("/belege/neu", data={**BELEG, "art": "einnahme", "kategorie": "umsatz_steuerpflichtig",
                                     "partner": "Kunde AG", "netto": "1000", "aktion": "festschreiben",
                                     "zahlungsdatum": f"{JAHR}-03-07"})
    html = client.get(f"/belege/?jahr={JAHR}").get_data(as_text=True)
    assert "1.190,00 €" in html and "119,00 €" in html and "1.071,00 €" in html
    assert "Kunde AG" not in client.get(f"/belege/?jahr={JAHR}&art=ausgabe").get_data(as_text=True)
    csv = client.get(f"/belege/export.csv?jahr={JAHR}").get_data(as_text=True)
    assert csv.startswith("﻿Belegnummer;") and f"E-{JAHR}-0001" in csv and "1190,00" in csv


def test_fremde_webseite_wird_abgewiesen(client, con):
    r = client.post("/belege/neu", data=BELEG, headers={"Origin": "https://boese.example"})
    assert r.status_code == 403 and belege(con) == []
    r = client.post("/belege/neu", data=BELEG, headers={"Origin": "http://localhost"})
    assert r.status_code == 302


# ------------------------------------------------------------------ XRechnung-Import

def test_xrechnung_parser():
    x = lese_xrechnung((BEISPIELE / "xrechnung_ubl.xml").read_bytes())
    assert (x.format, x.rechnungsnummer, x.verkaeufer) == ("UBL", "RE-2026-4711", "Muster Hosting GmbH")
    assert (x.netto_cent, x.ust_cent, x.brutto_cent, x.ust_satz) == (12000, 2280, 14280, "19")
    x = lese_xrechnung((BEISPIELE / "xrechnung_cii.xml").read_bytes())
    assert (x.format, x.rechnungsdatum, x.ust_satz) == ("CII", "2026-11-10", "mehrere")
    assert (x.netto_cent, x.ust_cent, x.brutto_cent) == (105000, 19350, 124350)
    for kaputt in (b"<html/>", b"kein xml",
                   b'<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;">]><l>&b;</l>'):
        with pytest.raises(XRechnungFehler):
            lese_xrechnung(kaputt)


def test_xrechnung_import(client, con):
    def einlesen(name):
        return client.post("/belege/xrechnung", content_type="multipart/form-data",
                           data={"xml": (io.BytesIO((BEISPIELE / name).read_bytes()), name)})
    assert einlesen("xrechnung_ubl.xml").status_code == 302
    (b,) = belege(con)
    assert (b["art"], b["partner"], b["brutto_cent"], b["status"], b["quelle"]) == \
        ("ausgabe", "Muster Hosting GmbH", 14280, "entwurf", "xrechnung")
    assert "bereits erfasst" in einlesen("xrechnung_ubl.xml").get_data(as_text=True) or len(belege(con)) == 1
    assert len(belege(con)) == 1


# ------------------------------------------------------------------ Übernahme aus „Rechnungen“

@pytest.fixture
def verz(app):
    return app.config["DATENVERZEICHNIS"]


@pytest.fixture
def kunde(verz, con):
    stammdaten.speichern(con, STAMMDATEN)
    return kunden.anlegen(con, verz, {"firma": "Bäckerei Sonnenschein GmbH", "strasse": "Lindenallee 14",
                                      "plz": "50667", "ort": "Köln", "land": "DE",
                                      "email": "buchhaltung@baeckerei.de"}, [])


def rechnung(con, verz, kunde, **mehr) -> int:
    rid = rechnungen.anlegen(con, verz, formular(kunde, **mehr))
    rechnungen.abschliessen(con, verz, rid, mit_xml=False)      # wie in der Testversion
    con.commit()
    return rid


def test_rechnung_wird_festgeschriebene_einnahme(con, verz, kunde):
    rid = rechnung(con, verz, kunde)
    erg = abgleich.abgleichen(con)
    (b,) = belege(con)
    assert erg["neu"] == [b["id"]]
    r = rechnungen.laden(con, rid)
    assert (b["art"], b["status"], b["quelle"], b["rechnung_id"]) == ("einnahme", "festgeschrieben", "rechnung", rid)
    assert b["partner"] == "Bäckerei Sonnenschein GmbH" and b["rechnungsnummer"] == r["nummer"]
    # 6 × 110 € zu 19 % + 500 × 0,125 € zu 7 % = 852,28 €
    assert (b["netto_cent"], b["ust_cent"], b["brutto_cent"], b["ust_satz"]) == (72250, 12978, 85228, "mehrere")
    assert b["kategorie"] == "umsatz_steuerpflichtig" and b["zahlungsdatum"] is None
    assert abgleich.abgleichen(con) == {"neu": [], "zahlungen": []}          # nur einmal


def test_zahlungseingang_und_ruecknahme(con, verz, kunde):
    rid = rechnung(con, verz, kunde)
    abgleich.abgleichen(con)
    rechnungen.bezahlt_setzen(con, rid, f"{JAHR}-02-01")
    con.commit()
    assert abgleich.abgleichen(con)["zahlungen"]
    assert belege(con)[0]["zahlungsdatum"] == f"{JAHR}-02-01"
    # Zahlung in „Rechnungen“ zurückgesetzt -> Beleg storniert und ohne Zahlung neu gebucht
    rechnungen.zahlung_zuruecksetzen(con, rid)
    con.commit()
    abgleich.abgleichen(con)
    alt, storno, neu = belege(con)
    assert (alt["status"], storno["quelle"], storno["zahlungsdatum"]) == ("storniert", "storno", f"{JAHR}-02-01")
    assert (neu["status"], neu["zahlungsdatum"], neu["rechnung_id"]) == ("festgeschrieben", None, rid)
    assert sum(b["brutto_cent"] for b in belege(con) if b["zahlungsdatum"]) == 0


def test_storno_einer_offenen_rechnung_ohne_geldfluss(con, verz, kunde):
    rid = rechnung(con, verz, kunde)
    abgleich.abgleichen(con)
    sid = rechnungen.storno_anlegen(con, rid, "Auftrag zurückgezogen", HEUTE)
    rechnungen.abschliessen(con, verz, sid, mit_xml=False)
    con.commit()
    abgleich.abgleichen(con)
    original, gegen = belege(con)
    assert original["status"] == "storniert" and gegen["rechnung_id"] == sid
    assert gegen["brutto_cent"] == -85228 and gegen["zahlungsdatum"] is None
    assert con.execute("SELECT art FROM rechnung_verbuchung WHERE rechnung_id = ?", (sid,)).fetchone()[0] == "verrechnet"


def test_storno_einer_bezahlten_rechnung_ist_erstattung(con, verz, kunde):
    rid = rechnung(con, verz, kunde)
    rechnungen.bezahlt_setzen(con, rid, f"{JAHR}-02-01")
    sid = rechnungen.storno_anlegen(con, rid, "Reklamation", HEUTE)
    rechnungen.abschliessen(con, verz, sid, mit_xml=False)
    con.commit()
    abgleich.abgleichen(con)
    original, erstattung = belege(con)
    assert original["status"] == "festgeschrieben" and original["zahlungsdatum"] == f"{JAHR}-02-01"
    assert erstattung["brutto_cent"] == -85228 and erstattung["zahlungsdatum"] is None
    rechnungen.bezahlt_setzen(con, sid, HEUTE)                     # Erstattung überwiesen
    con.commit()
    abgleich.abgleichen(con)
    assert belege(con)[1]["zahlungsdatum"] == HEUTE


def test_korrektur_einer_offenen_rechnung(con, verz, kunde):
    rid = rechnung(con, verz, kunde)
    abgleich.abgleichen(con)
    kid = rechnungen.korrektur_anlegen(con, rid)
    rechnungen.aendern(con, verz, kid, formular(kunde, "8", korrektur_grund="Stundenzahl falsch", rechnungsdatum=HEUTE))
    rechnungen.abschliessen(con, verz, kid, mit_xml=False)
    con.commit()
    abgleich.abgleichen(con)
    k = rechnungen.laden(con, kid)
    aktive = [b for b in belege(con) if b["status"] == "festgeschrieben" and b["quelle"] == "rechnung"]
    assert [b["rechnung_id"] for b in aktive] == [kid]
    assert aktive[0]["brutto_cent"] == int(round(float(k["summe_gesamt"]) * 100))
    # Summe aller Belege = Betrag der Korrektur
    assert sum(b["brutto_cent"] for b in belege(con)) == aktive[0]["brutto_cent"]


def test_korrektur_einer_bezahlten_rechnung_bucht_differenz(con, verz, kunde):
    rid = rechnung(con, verz, kunde)
    rechnungen.bezahlt_setzen(con, rid, f"{JAHR}-02-01")
    con.commit()
    abgleich.abgleichen(con)
    kid = rechnungen.korrektur_anlegen(con, rid)
    rechnungen.aendern(con, verz, kid, formular(kunde, "8", korrektur_grund="Zwei Stunden mehr", rechnungsdatum=HEUTE))
    rechnungen.abschliessen(con, verz, kid, mit_xml=False)
    con.commit()
    abgleich.abgleichen(con)
    k, o = rechnungen.laden(con, kid), rechnungen.laden(con, rid)
    original = belege(con)[0]
    assert original["status"] == "festgeschrieben"            # bleibt – das Geld ist geflossen
    diff = int(round((float(k["summe_gesamt"]) - float(o["summe_gesamt"])) * 100))
    if diff:
        assert belege(con)[-1]["brutto_cent"] == diff and belege(con)[-1]["rechnung_id"] == kid
    else:
        assert con.execute("SELECT art FROM rechnung_verbuchung WHERE rechnung_id = ?", (kid,)).fetchone()[0] == "ohne_beleg"


def test_liste_uebernimmt_rechnungen_automatisch(client, con, verz, kunde):
    rechnung(con, verz, kunde)
    html = client.get("/belege/").get_data(as_text=True)
    assert "1 abgeschlossene Rechnung als Einnahme übernommen" in html and "aus Rechnungen" in html
