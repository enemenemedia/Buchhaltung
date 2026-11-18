"""EÜR: Zufluss/Abfluss, USt/Vorsteuer, Bewirtung, GWG, AfA, Kleinunternehmer, Ausgaben."""

import pytest

from app import create_app, db
from app.belege import abgleich, service
from app.euer import service as euer
from app.kunden import service as kunden
from app.rechnungen import service as rechnungen
from app.stammdaten import service as stammdaten
from tests.test_rechnungen import STAMMDATEN
from tests.test_storno_korrektur import formular

J = 2026


@pytest.fixture
def app(tmp_path):
    return create_app(tmp_path)


@pytest.fixture
def verz(app):
    return app.config["DATENVERZEICHNIS"]


@pytest.fixture
def con(app):
    c = db.verbinden(app.config["DATENBANK"])
    stammdaten.speichern(c, STAMMDATEN)
    c.commit()
    yield c
    c.close()


def buchen(con, verz, festschreiben=True, **w):
    basis = {"art": "ausgabe", "belegdatum": f"{J}-03-01", "partner": "Lieferant", "beschreibung": "Test",
             "kategorie": "buero", "ust_satz": "19"}
    bid = service.anlegen(con, verz, service.formular_auswerten({**basis, **w}), festschreiben_sofort=festschreiben)
    con.commit()
    return bid


def zeilen(liste):
    return {z.schluessel: z.cent for z in liste}


def test_afa_plan_monatsgenau():
    assert euer.afa_plan(360000, 3, "2026-04-15") == {2026: 90000, 2027: 120000, 2028: 120000, 2029: 30000}
    plan = euer.afa_plan(100000, 3, "2026-01-01")          # Rundung: Summe bleibt exakt
    assert plan == {2026: 33333, 2027: 33334, 2028: 33333} and sum(plan.values()) == 100000


def test_euer_alle_regeln(con, verz):
    bezahlt = {"zahlungsdatum": f"{J}-05-10"}
    buchen(con, verz, art="einnahme", kategorie="umsatz_steuerpflichtig", netto="1000", **bezahlt)
    buchen(con, verz, art="einnahme", kategorie="ust_erstattung", netto="50", **bezahlt)
    buchen(con, verz, netto="100", **bezahlt)                                          # Büro
    buchen(con, verz, kategorie="bewirtung", netto="100", **bezahlt)
    buchen(con, verz, kategorie="gwg", netto="500", **bezahlt)
    buchen(con, verz, kategorie="anlagevermoegen", netto="3600", nutzungsdauer="3", belegdatum=f"{J}-04-15", **bezahlt)
    buchen(con, verz, kategorie="ust_zahlung", netto="300", **bezahlt)
    # nicht enthalten: ohne Zahlung, Entwurf, Zahlung im Vorjahr, storniertes Paar
    buchen(con, verz, art="einnahme", kategorie="umsatz_steuerpflichtig", netto="777")
    buchen(con, verz, festschreiben=False, netto="999", **bezahlt)
    buchen(con, verz, netto="888", zahlungsdatum=f"{J - 1}-12-30", belegdatum=f"{J - 1}-12-30")
    sid = buchen(con, verz, netto="555", **bezahlt)
    service.stornieren(con, sid, "doppelt")
    con.commit()

    # Standard: auch ohne Festschreibung – der Entwurf über 999 € (Büro) ist enthalten
    alle = euer.berechnen(con, J)
    assert zeilen(alle.ausgaben)["buero"] == 10000 + 99900
    assert zeilen(alle.ausgaben)["vorsteuer"] == 81700 + 18981
    assert any("noch nicht festgeschrieben (Entwurf), aber enthalten" in h for h in alle.hinweise)

    erg = euer.berechnen(con, J, nur_festgeschrieben=True)
    assert zeilen(erg.einnahmen) == {"umsatz_steuerpflichtig": 100000, "ust_vereinnahmt": 19000, "ust_erstattung": 5000}
    assert zeilen(erg.ausgaben) == {"buero": 10000, "bewirtung": 7000, "afa": 90000, "gwg": 50000,
                                    "vorsteuer": 1900 + 1900 + 9500 + 68400, "ust_zahlung": 30000}
    assert erg.summe_einnahmen == 124000
    assert erg.summe_ausgaben == 10000 + 7000 + 90000 + 50000 + 81700 + 30000
    assert erg.gewinn == 124000 - 268700
    (a,) = erg.anlagen
    assert (a.ak_cent, a.afa_bisher, a.afa_jahr, a.restwert) == (360000, 0, 90000, 270000)
    assert len(erg.hinweise) == 2                   # Entwurf, ohne Zahlung
    assert sum(m["ust"] for m in erg.monate) == 19000

    # Folgejahr: nur AfA, keine Zahlungen
    erg2 = euer.berechnen(con, J + 1, nur_festgeschrieben=True)
    assert zeilen(erg2.ausgaben) == {"afa": 120000}
    assert erg2.anlagen[0].afa_bisher == 90000 and erg2.anlagen[0].restwert == 150000


def test_kleinunternehmer_ohne_vorsteuer(con, verz):
    con.execute("UPDATE stammdaten SET kleinunternehmer = 1")
    con.commit()
    buchen(con, verz, netto="100", zahlungsdatum=f"{J}-02-01")
    erg = euer.berechnen(con, J)
    assert zeilen(erg.ausgaben) == {"buero": 11900}


def test_bezahlte_rechnung_erscheint_in_euer(con, verz):
    kid = kunden.anlegen(con, verz, {"firma": "Kunde GmbH", "strasse": "Weg 1", "plz": "10115", "ort": "Berlin",
                                     "land": "DE", "email": "a@b.de"}, [])
    rid = rechnungen.anlegen(con, verz, formular(kid, rechnungsdatum=f"{J}-01-10"))
    rechnungen.abschliessen(con, verz, rid, mit_xml=False)
    rechnungen.bezahlt_setzen(con, rid, f"{J}-01-25")
    con.commit()
    abgleich.abgleichen(con)
    erg = euer.berechnen(con, J)
    assert zeilen(erg.einnahmen) == {"umsatz_steuerpflichtig": 72250, "ust_vereinnahmt": 12978}
    assert erg.monate[0]["einnahmen"] == 85228


def test_seite_csv_pdf(app, con, verz):
    buchen(con, verz, art="einnahme", kategorie="umsatz_steuerpflichtig", netto="1000", zahlungsdatum=f"{J}-05-10")
    c = app.test_client()
    html = c.get(f"/euer/?jahr={J}").get_data(as_text=True)
    assert "Einnahmen-Überschuss-Rechnung 2026" in html and "1.190,00 €" in html and "Vereinnahmte Umsatzsteuer" in html
    csv = c.get(f"/euer/{J}.csv").get_data(as_text=True)
    assert "Gewinn / Verlust;1190,00" in csv
    pdf = c.get(f"/euer/{J}.pdf")
    assert pdf.status_code == 200 and pdf.data.startswith(b"%PDF")
