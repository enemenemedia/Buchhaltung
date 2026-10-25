import io
from decimal import Decimal

import pytest
from openpyxl import Workbook, load_workbook

from app import create_app, db, zahlen
from app.leistungen import importer, service
from app.leistungen.importer import ImportFehler
from app.leistungen.service import ValidierungsFehler


@pytest.fixture
def app(tmp_path):
    return create_app(tmp_path)


@pytest.fixture
def con(app):
    c = db.verbinden(app.config["DATENBANK"])
    yield c
    c.close()


@pytest.fixture
def client(app):
    return app.test_client()


BERATUNG = {"bezeichnung": "Beratung", "art": "zeit", "einheit_code": "HUR", "preis": "95,00", "anzahl": "1"}


def csv_datei(text: str, kodierung="utf-8") -> bytes:
    return text.encode(kodierung)


def xlsx_datei(zeilen) -> bytes:
    mappe = Workbook()
    for z in zeilen:
        mappe.active.append(z)
    puffer = io.BytesIO()
    mappe.save(puffer)
    return puffer.getvalue()


# --- Zahlen ----------------------------------------------------------------

@pytest.mark.parametrize("text, erwartet", [
    ("85", "85"), ("85,5", "85.5"), ("85.50", "85.50"), ("1.234,56", "1234.56"), ("1,234.56", "1234.56"),
    ("1 234,56 €", "1234.56"), ("1.5", "1.5"), ("1.234.567", "1234567"), ("0,125", "0.125"), (12.5, "12.5"),
])
def test_zahlen_parsen(text, erwartet):
    assert zahlen.parsen(text) == Decimal(erwartet)


@pytest.mark.parametrize("text", ["", "abc", "1,2,3", "12a", "--5"])
def test_zahlen_ungueltig(text):
    with pytest.raises(zahlen.ZahlFehler):
        zahlen.parsen(text)


def test_zahlen_formatieren():
    assert zahlen.geld("1234.5") == "1.234,50 €"
    assert zahlen.geld("0.125") == "0,125 €"
    assert zahlen.geld("95") == "95,00 €"
    assert zahlen.menge("1.5") == "1,5"
    assert zahlen.menge("2") == "2"
    assert zahlen.menge("1000") == "1.000"
    assert zahlen.speicherform(Decimal("95.00")) == "95"


# --- Leistungen ------------------------------------------------------------

def test_anlegen_mit_automatischer_nummer(con):
    lid = service.anlegen(con, BERATUNG)
    l = service.laden(con, lid)
    assert l["nummer"] == "L0001"
    assert (l["preis"], l["anzahl"], l["ust_satz"]) == ("95", "1", "19")
    assert service.anlegen(con, {**BERATUNG, "bezeichnung": "Zwei"}) and service.naechste_nummer(con) == "L0003"
    assert service.verlauf(con, lid)[0]["aktion"] == "angelegt"


@pytest.mark.parametrize("art, einheit, preis, anzahl, gesamt", [
    ("zeit", "HUR", "95", "2,5", "237.50"),
    ("zeit", "DAY", "720", "1", "720.00"),
    ("stueck", "H87", "0,125", "1000", "125.00"),
    ("pauschal", "LS", "149", "1", "149.00"),
])
def test_arten_und_gesamtpreis(con, art, einheit, preis, anzahl, gesamt):
    lid = service.anlegen(con, {"bezeichnung": "X", "art": art, "einheit_code": einheit, "preis": preis, "anzahl": anzahl})
    l = service.laden(con, lid)
    assert service.gesamt(l["preis"], l["anzahl"]) == Decimal(gesamt)


def test_einheit_wird_vorbelegt(con):
    lid = service.anlegen(con, {"bezeichnung": "Paket", "art": "pauschal", "preis": "10"})
    assert service.laden(con, lid)["einheit_code"] == "LS"


@pytest.mark.parametrize("eingabe, feld", [
    ({"bezeichnung": ""}, "bezeichnung"),
    ({"art": "kilo"}, "art"),
    ({"art": "stueck", "einheit_code": "HUR"}, "einheit_code"),
    ({"preis": "-5"}, "preis"),
    ({"preis": "abc"}, "preis"),
    ({"preis": "1,23456"}, "preis"),
    ({"anzahl": "0"}, "anzahl"),
    ({"ust_satz": "16"}, "ust_satz"),
])
def test_validierung(con, eingabe, feld):
    with pytest.raises(ValidierungsFehler) as e:
        service.anlegen(con, {**BERATUNG, **eingabe})
    assert feld in e.value.fehler


def test_nummer_eindeutig_ohne_gross_klein(con):
    service.anlegen(con, {**BERATUNG, "nummer": "B-100"})
    with pytest.raises(ValidierungsFehler) as e:
        service.anlegen(con, {**BERATUNG, "nummer": "b-100"})
    assert "bereits vergeben" in e.value.fehler["nummer"]


def test_aendern_protokolliert_preisaenderung(con):
    lid = service.anlegen(con, BERATUNG)
    assert service.aendern(con, lid, {**BERATUNG, "nummer": "L0001"}) is False
    assert service.aendern(con, lid, {**BERATUNG, "preis": "105", "anzahl": "1,5"})
    eintrag = service.verlauf(con, lid)[0]
    assert eintrag["aktion"] == "geaendert"
    assert eintrag["details"]["felder"]["Einzelpreis"] == ["95,00 €", "105,00 €"]
    assert eintrag["details"]["felder"]["Anzahl"] == ["1", "1,5"]


def test_deaktivieren_und_loeschen(con):
    lid = service.anlegen(con, BERATUNG)
    service.aktiv_setzen(con, lid, False)
    assert service.liste(con) == [] and len(service.liste(con, status="inaktiv")) == 1
    service.loeschen(con, lid)
    assert service.liste(con, status="alle") == []
    aktionen = [e["aktion"] for e in service.verlauf(con, lid)]
    assert aktionen == ["geloescht", "deaktiviert", "angelegt"]


def test_suche_und_filter(con):
    service.anlegen(con, {**BERATUNG, "kategorie": "Beratung"})
    service.anlegen(con, {"bezeichnung": "Flyer", "art": "stueck", "preis": "1", "kategorie": "Druck"})
    assert [l["bezeichnung"] for l in service.liste(con, art="stueck")] == ["Flyer"]
    assert [l["bezeichnung"] for l in service.liste(con, suche="bera")] == ["Beratung"]
    assert service.kategorien(con) == ["Beratung", "Druck"]


# --- Import ----------------------------------------------------------------

CSV_DEUTSCH = """Nr;Bezeichnung;Art;Einheit;Preis netto (€);Menge;MwSt;Unbekannt
B1;Beratung;Zeiteinheit;Std;95,00;1;19;x
B2;Projekttag;;Tag;1.250,50;;7;
D1;Flyer A5;Stück;Stk;0,125;500;;
P1;Einrichtung;Pauschal;;149;1;19;
"""


def test_csv_import_deutsch(con):
    roh = importer.einlesen("leistungen.csv", csv_datei(CSV_DEUTSCH, "cp1252"))
    assert roh.zuordnung["preis"] == "Preis netto (€)"
    assert roh.ignoriert == ["Unbekannt"]
    v = importer.vorschau(con, roh)
    assert [z.status for z in v.zeilen] == ["neu"] * 4
    tag = v.zeilen[1].daten
    assert (tag["art"], tag["einheit_code"], tag["preis"], tag["anzahl"], tag["ust_satz"]) == ("zeit", "DAY", "1250.5", "1", "7")
    flyer = v.zeilen[2].daten
    assert (flyer["art"], flyer["einheit_code"], flyer["preis"]) == ("stueck", "H87", "0.125")
    erg = importer.uebernehmen(con, roh, bestehende_aktualisieren=True)
    assert erg.angelegt == 4
    assert service.nach_nummer(con, "P1")["einheit_code"] == "LS"
    assert "Importiert aus leistungen.csv" in service.verlauf(con, service.nach_nummer(con, "B1")["id"])[0]["details"]["hinweise"]


def test_csv_mit_komma_und_utf8_bom(con):
    text = "﻿bezeichnung,preis,einheit\nWartung,\"1,234.50\",Monat\n"
    roh = importer.einlesen("x.csv", text.encode("utf-8"))
    z = importer.vorschau(con, roh).zeilen[0]
    assert z.status == "neu" and z.daten["preis"] == "1234.5" and z.daten["einheit_code"] == "MON"


def test_xlsx_import_mit_zahlen_und_prozent(con):
    datei = xlsx_datei([
        [None], ["Artikelnummer", "Leistung", "Einheit", "Einzelpreis", "Anzahl", "USt"],
        ["W1", "Webdesign", "Stunde", 85.0, 2.5, 0.19],
        ["W2", "Domain", "Pauschale", 12, None, 0.07],
    ])
    roh = importer.einlesen("liste.xlsx", datei)
    zeilen = importer.vorschau(con, roh).zeilen
    assert [z.status for z in zeilen] == ["neu", "neu"]
    assert (zeilen[0].daten["preis"], zeilen[0].daten["anzahl"], zeilen[0].daten["ust_satz"]) == ("85", "2.5", "19")
    assert zeilen[0].zeile == 3
    assert zeilen[1].daten["ust_satz"] == "7"


def test_import_fehler_und_dubletten(con):
    text = ("Nummer;Bezeichnung;Art;Preis\n"
            "A;Gut;Pauschal;10\n"
            "B;;Pauschal;10\n"           # Bezeichnung fehlt
            "C;Kaputt;Kilo;10\n"         # Art unbekannt
            "D;Preis falsch;Stück;zehn\n"
            "A;Doppelt;Pauschal;10\n"
            "E;Ohne Art;;10\n")
    v = importer.vorschau(con, importer.einlesen("f.csv", text.encode()))
    assert [z.status for z in v.zeilen] == ["neu", "fehler", "fehler", "fehler", "doppelt", "fehler"]
    assert "Kilo" in v.zeilen[2].fehler[0]
    assert v.zeilen[3].anzeige("preis") == "zehn"
    erg = importer.uebernehmen(con, v.rohdaten, bestehende_aktualisieren=True)
    assert (erg.angelegt, erg.fehlerhaft) == (1, 5)


def test_import_aktualisiert_oder_ueberspringt_bestand(con):
    service.anlegen(con, {**BERATUNG, "nummer": "B1"})
    text = "Nummer;Bezeichnung;Art;Einheit;Preis\nB1;Beratung;Zeit;Stunde;110\nB2;Neu;Pauschal;;5\n"
    roh = importer.einlesen("u.csv", text.encode())
    v = importer.vorschau(con, roh)
    assert v.zeilen[0].status == "aktualisieren"
    assert v.zeilen[0].aenderungen == {"Einzelpreis": ["95,00 €", "110,00 €"]}

    erg = importer.uebernehmen(con, roh, bestehende_aktualisieren=False)
    assert (erg.angelegt, erg.aktualisiert, erg.uebersprungen) == (1, 0, 1)
    assert service.nach_nummer(con, "B1")["preis"] == "95"

    erg = importer.uebernehmen(con, roh, bestehende_aktualisieren=True)
    assert (erg.angelegt, erg.aktualisiert, erg.unveraendert) == (0, 1, 1)
    assert service.nach_nummer(con, "B1")["preis"] == "110"


@pytest.mark.parametrize("name, inhalt, meldung", [
    ("x.pdf", b"%PDF", "CSV- oder Excel"),
    ("x.xls", b"\xd0\xcf", ".xls wird nicht"),
    ("x.csv", b"", "leer"),
    ("x.csv", b"Name;Text\nA;B\n", "Preis"),
    ("x.csv", b"Preis;Menge\n1;2\n", "Bezeichnung"),
    ("x.csv", b"Bezeichnung;Preis\n", "nur eine Kopfzeile"),
    ("x.xlsx", b"kein excel", "nicht geöffnet"),
])
def test_ungueltige_dateien(name, inhalt, meldung):
    with pytest.raises(ImportFehler, match=meldung):
        importer.einlesen(name, inhalt)


def test_export_und_reimport_sind_verlustfrei(con):
    service.anlegen(con, {**BERATUNG, "beschreibung": "Vor Ort; auch remote", "kategorie": "Beratung", "preis": "1234,5678"})
    service.anlegen(con, {"bezeichnung": "Flyer", "art": "stueck", "preis": "0,125", "anzahl": "500", "ust_satz": "7"})
    vorher = [dict(l) for l in service.liste(con)]
    for fmt in ("csv", "xlsx"):
        datei = importer.export(service.liste(con), fmt)
        v = importer.vorschau(con, importer.einlesen(f"export.{fmt}", datei))
        assert [z.status for z in v.zeilen] == ["unveraendert", "unveraendert"], fmt
    assert [dict(l) for l in service.liste(con)] == vorher


def test_vorlage_ist_importierbar(con):
    for fmt in ("csv", "xlsx"):
        v = importer.vorschau(con, importer.einlesen(f"vorlage.{fmt}", importer.vorlage(fmt)))
        assert [z.status for z in v.zeilen] == ["neu"] * 4, fmt
    mappe = load_workbook(io.BytesIO(importer.vorlage("xlsx")))
    assert mappe.active["G2"].value == 95.0


# --- Weboberfläche ---------------------------------------------------------

def test_web_anlegen_bearbeiten(client):
    assert client.get("/leistungen/").status_code == 200
    r = client.post("/leistungen/neu", data={**BERATUNG, "preis": "abc"})
    assert r.status_code == 422 and "keine gültige Zahl" in r.get_data(as_text=True)
    r = client.post("/leistungen/neu", data=BERATUNG)
    assert r.status_code == 302
    seite = client.get(r.headers["Location"]).get_data(as_text=True)
    assert 'value="95,00"' in seite and "Änderungsprotokoll" in seite
    liste = client.get("/leistungen/").get_data(as_text=True)
    assert "95,00 €" in liste and "Beratung" in liste

    client.post("/leistungen/1", data={**BERATUNG, "nummer": "L0001", "preis": "99"})
    assert "99,00 €" in client.get("/leistungen/").get_data(as_text=True)
    client.post("/leistungen/1/aktiv", data={"aktiv": "0"})
    assert "Beratung" not in client.get("/leistungen/").get_data(as_text=True)
    client.post("/leistungen/1/loeschen")
    assert client.get("/leistungen/1").status_code == 404


def test_web_import_ablauf(client, app):
    r = client.post("/leistungen/import", data={"datei": (io.BytesIO(CSV_DEUTSCH.encode()), "l.csv")},
                    content_type="multipart/form-data")
    seite = r.get_data(as_text=True)
    assert r.status_code == 200 and "Vorschau" in seite and "Import ausführen" in seite
    token = seite.split("/leistungen/import/")[1].split('"')[0]

    r = client.post(f"/leistungen/import/{token}", data={"aktion": "importieren", "bestehende": "aktualisieren"},
                    follow_redirects=True)
    assert "4 neu angelegt" in r.get_data(as_text=True)
    # Token ist verbraucht
    r = client.post(f"/leistungen/import/{token}", data={"aktion": "importieren"}, follow_redirects=True)
    assert "abgelaufen" in r.get_data(as_text=True)
    r = client.post("/leistungen/import/../../etc", data={"aktion": "importieren"})
    assert r.status_code in (302, 404)


def test_web_import_fehlerhafte_datei(client):
    r = client.post("/leistungen/import", data={"datei": (io.BytesIO(b"x"), "l.pdf")},
                    content_type="multipart/form-data", follow_redirects=True)
    assert "CSV- oder Excel" in r.get_data(as_text=True)


def test_web_export_und_vorlage(client):
    client.post("/leistungen/neu", data=BERATUNG)
    r = client.get("/leistungen/export.csv")
    assert r.mimetype == "text/csv" and "attachment" in r.headers["Content-Disposition"]
    assert "L0001;Beratung;;;Zeiteinheit;Stunde;95,00;1;19\r\n" in r.data.decode("utf-8-sig")
    assert r.data.startswith(b"\xef\xbb\xbf")  # BOM für Excel
    assert client.get("/leistungen/export.xlsx").data[:2] == b"PK"
    assert client.get("/leistungen/vorlage.xlsx").status_code == 200
    assert client.get("/leistungen/export.exe").status_code == 404
