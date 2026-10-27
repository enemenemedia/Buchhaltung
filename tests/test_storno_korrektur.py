import sqlite3
from datetime import date
from xml.etree import ElementTree as ET

import pytest

from app import create_app, db, zahlen
from app.kunden import service as kunden
from app.rechnungen import service, xrechnung
from app.rechnungen.service import RechnungsFehler, ValidierungsFehler
from app.stammdaten import service as stammdaten
from tests.test_rechnungen import STAMMDATEN, offiziell_pruefen

NS = {"cbc": xrechnung.NS_CBC, "cac": xrechnung.NS_CAC}
JAHR = date.today().year
HEUTE = date.today().isoformat()


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
    yield c
    c.close()


@pytest.fixture
def client(app, con, lizenzieren):
    lizenzieren(app)
    return app.test_client()


@pytest.fixture
def kunde(verz, con):
    return kunden.anlegen(con, verz, {"firma": "Bäckerei Sonnenschein GmbH", "strasse": "Lindenallee 14",
                                      "plz": "50667", "ort": "Köln", "land": "DE", "email": "buchhaltung@baeckerei.de"}, [])


def formular(kid, stunden="6", **mehr):
    f = {"kunde_modus": "bestand", "kunde_id": str(kid), "ansprechpartner_id": "", "rechnungsdatum": "2026-01-10",
         "leistung_von": "2026-01-05", "leistung_bis": "", "zahlungsziel_tage": "14", "betreff": "Website",
         "anmerkungen": "", "kaeufer_referenz": "", "bestellnummer": "", "befreiung_art": "E", "befreiung_grund": "",
         "pos-0-bezeichnung": "Beratung", "pos-0-einheit_code": "HUR", "pos-0-menge": stunden,
         "pos-0-einzelpreis": "110", "pos-0-ust_satz": "19",
         "pos-1-bezeichnung": "Flyer", "pos-1-einheit_code": "H87", "pos-1-menge": "500",
         "pos-1-einzelpreis": "0,125", "pos-1-ust_satz": "7"}
    f.update(mehr)
    return f


@pytest.fixture
def rechnung(verz, con, kunde):
    rid = service.anlegen(con, verz, formular(kunde))
    service.abschliessen(con, verz, rid)
    return rid                                             # RE-…-0001 über 852,28 €


def wert(pfad_xml, xpath):
    return [e.text for e in ET.parse(pfad_xml).getroot().findall(xpath, NS)]


# --- Stornorechnung ------------------------------------------------------------------------

def test_storno_einer_offenen_rechnung(verz, con, kunde, rechnung):
    sid = service.storno_anlegen(con, rechnung, "Auftrag zurückgezogen", HEUTE)
    s = service.laden(con, sid)
    assert (s["art"], s["bezug_id"], s["kunde_id"], s["status"]) == ("storno", rechnung, kunde, "entwurf")
    assert s["summe_gesamt"] == "-852.28"
    xml, pdf = service.abschliessen(con, verz, sid)
    s, o = service.laden(con, sid), service.laden(con, rechnung)

    # Nummer, Zuordnung und Dateiname
    assert s["nummer"] == f"ST-{JAHR}-0001"
    assert xml.name == f"{HEUTE}_ST-{JAHR}-0001_K00001_storno_zu_RE-{JAHR}-0001.xml"
    assert pdf == xml.with_suffix(".pdf") and pdf.read_bytes().startswith(b"%PDF")
    assert xml.parent == verz / "kunden" / "K00001" / "rechnungen"
    # Status: Original storniert, Storno durch Verrechnung erledigt
    assert service.anzeige_status(o) == "storniert" and service.anzeige_status(s) == "erledigt"
    assert service.summen_offen(con)["offen"] == 0
    # XRechnung: Gutschrift 381 mit Bezug auf das Original, positive Beträge, Verrechnung (97)
    root = ET.parse(xml).getroot()
    assert root.tag == f"{{{xrechnung.NS_CN}}}CreditNote"
    assert wert(xml, "cbc:CreditNoteTypeCode") == ["381"]
    assert wert(xml, "cac:BillingReference/cac:InvoiceDocumentReference/cbc:ID") == [f"RE-{JAHR}-0001"]
    assert wert(xml, "cac:BillingReference/cac:InvoiceDocumentReference/cbc:IssueDate") == ["2026-01-10"]
    assert wert(xml, "cac:LegalMonetaryTotal/cbc:PayableAmount") == ["852.28"]
    assert wert(xml, "cac:PaymentMeans/cbc:PaymentMeansCode") == ["97"]
    assert any("Auftrag zurückgezogen" in n for n in wert(xml, "cbc:Note"))
    offiziell_pruefen(xml.read_bytes())
    # Protokoll am Original
    assert service.verlauf(con, rechnung)[0]["aktion"] == "storniert"


def test_storno_einer_bezahlten_rechnung_erstattung(verz, con, rechnung):
    service.bezahlt_setzen(con, rechnung, "2026-01-20")
    sid = service.storno_anlegen(con, rechnung, "Doppelt berechnet", HEUTE)
    xml, _ = service.abschliessen(con, verz, sid)
    s = service.laden(con, sid)
    assert service.anzeige_status(s) == "erstattung_offen"
    assert service.summen_offen(con) == {"offen": 0, "ueberfaellig": 0, "erstattung": zahlen.parsen("852.28")}
    assert wert(xml, "cac:PaymentMeans/cbc:PaymentMeansCode") == ["ZZZ"]
    offiziell_pruefen(xml.read_bytes())
    service.bezahlt_setzen(con, sid, HEUTE)                       # Erstattung erfolgt
    assert service.anzeige_status(service.laden(con, sid)) == "erledigt"
    assert service.summen_offen(con)["erstattung"] == 0


def test_storno_validierung_und_schutz(verz, con, kunde, rechnung):
    with pytest.raises(ValidierungsFehler):
        service.storno_anlegen(con, rechnung, "  ")
    with pytest.raises(ValidierungsFehler):
        service.storno_anlegen(con, rechnung, "Grund", "2025-12-31")     # vor dem Original
    entwurf = service.anlegen(con, verz, formular(kunde))
    with pytest.raises(RechnungsFehler, match="Entwürfe"):
        service.storno_anlegen(con, entwurf, "Grund")
    sid = service.storno_anlegen(con, rechnung, "Grund")
    with pytest.raises(RechnungsFehler, match="bereits"):
        service.korrektur_anlegen(con, rechnung)                       # nur ein Folgebeleg
    with pytest.raises(RechnungsFehler):
        service.aendern(con, verz, sid, formular(kunde))                # Storno nicht bearbeitbar
    service.loeschen(con, sid)                                          # Entwurf löschen gibt frei
    sid = service.storno_anlegen(con, rechnung, "Grund")
    service.abschliessen(con, verz, sid)
    with pytest.raises(RechnungsFehler, match="Stornorechnung kann nicht"):
        service.storno_anlegen(con, sid, "Grund")
    with pytest.raises(RechnungsFehler, match="ersetzt"):
        service.bezahlt_setzen(con, rechnung, HEUTE)


def test_datenbank_verhindert_fremde_zuordnung(verz, con, rechnung):
    anderer = kunden.anlegen(con, verz, {"firma": "Andere", "land": "DE"}, [])
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO rechnungen (kunde_id, art, bezug_id, rechnungsdatum, leistung_von, faellig_am) "
                    "VALUES (?, 'storno', ?, '2026-02-01', '2026-02-01', '2026-02-01')", (anderer, rechnung))
    con.rollback()
    sid = service.storno_anlegen(con, rechnung, "Grund")
    service.abschliessen(con, verz, sid)
    for sql in ("UPDATE rechnungen SET bezug_id = NULL WHERE id = ?", "UPDATE rechnungen SET art = 'rechnung' WHERE id = ?",
                "UPDATE rechnungen SET korrektur_grund = 'x' WHERE id = ?"):
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(sql, (sid,))
    con.rollback()


# --- Rechnungskorrektur ----------------------------------------------------------------------

def test_korrektur_einer_offenen_rechnung(verz, con, kunde, rechnung):
    kid = service.korrektur_anlegen(con, rechnung)
    k = service.laden(con, kid)
    assert (k["art"], k["bezug_id"], k["kunde_id"], k["status"]) == ("korrektur", rechnung, kunde, "entwurf")
    assert [p["menge"] for p in service.positionen(con, kid)] == ["6", "500"]
    # Grund ist Pflicht, Kunde lässt sich nicht ändern
    with pytest.raises(ValidierungsFehler) as e:
        service.aendern(con, verz, kid, formular(kunde, "4"))
    assert "korrektur_grund" in e.value.fehler
    anderer = kunden.anlegen(con, verz, {"firma": "Andere", "land": "DE"}, [])
    service.aendern(con, verz, kid, formular(anderer, "4", korrektur_grund="Falsche Stundenzahl",
                                             rechnungsdatum=HEUTE))
    assert service.laden(con, kid)["kunde_id"] == kunde
    xml, pdf = service.abschliessen(con, verz, kid)
    k, o = service.laden(con, kid), service.laden(con, rechnung)
    assert k["nummer"] == f"KR-{JAHR}-0001"
    assert xml.name == f"{HEUTE}_KR-{JAHR}-0001_K00001_korrektur_zu_RE-{JAHR}-0001.xml"
    assert service.anzeige_status(o) == "korrigiert" and service.anzeige_status(k) == "offen"
    assert wert(xml, "cbc:InvoiceTypeCode") == ["384"]
    assert wert(xml, "cac:BillingReference/cac:InvoiceDocumentReference/cbc:ID") == [f"RE-{JAHR}-0001"]
    assert wert(xml, "cac:LegalMonetaryTotal/cbc:PayableAmount") == ["590.48"]
    assert wert(xml, "cac:LegalMonetaryTotal/cbc:PrepaidAmount") == []
    assert service.summen_offen(con)["offen"] == zahlen.parsen("590.48")
    offiziell_pruefen(xml.read_bytes())


@pytest.mark.parametrize("stunden, zahlbar, status", [("4", "-261.80", "erstattung_offen"),
                                                      ("8", "261.80", "offen"),
                                                      ("6", "0.00", "bezahlt")])
def test_korrektur_einer_bezahlten_rechnung(verz, con, kunde, rechnung, stunden, zahlbar, status):
    service.bezahlt_setzen(con, rechnung, "2026-01-20")
    kid = service.korrektur_anlegen(con, rechnung)
    service.aendern(con, verz, kid, formular(kunde, stunden, korrektur_grund="Stunden angepasst", betreff="Website neu",
                                             rechnungsdatum=HEUTE))
    xml, _ = service.abschliessen(con, verz, kid)
    k = service.laden(con, kid)
    assert k["vorausbezahlt"] == "852.28"
    assert service.anzeige_status(k) == status
    assert wert(xml, "cac:LegalMonetaryTotal/cbc:PrepaidAmount") == ["852.28"]
    assert wert(xml, "cac:LegalMonetaryTotal/cbc:PayableAmount") == [zahlbar]
    offiziell_pruefen(xml.read_bytes())


def test_korrektur_einer_korrektur_und_belegkette(verz, con, kunde, rechnung):
    k1 = service.korrektur_anlegen(con, rechnung)
    service.aendern(con, verz, k1, formular(kunde, "4", korrektur_grund="weniger"))
    service.abschliessen(con, verz, k1)
    k2 = service.korrektur_anlegen(con, k1)
    service.aendern(con, verz, k2, formular(kunde, "5", korrektur_grund="doch mehr"))
    xml, _ = service.abschliessen(con, verz, k2)
    assert xml.name.endswith(f"_KR-{JAHR}-0002_K00001_korrektur_zu_KR-{JAHR}-0001.xml")
    kette = [b["nummer"] for b in service.belegkette(con, service.laden(con, rechnung))]
    assert kette == [f"RE-{JAHR}-0001", f"KR-{JAHR}-0001", f"KR-{JAHR}-0002"]
    s = service.storno_anlegen(con, k2, "Projekt abgebrochen")
    xml, _ = service.abschliessen(con, verz, s)
    assert xml.name.endswith(f"_ST-{JAHR}-0001_K00001_storno_zu_KR-{JAHR}-0002.xml")
    assert service.summen_offen(con)["offen"] == 0
    offiziell_pruefen(xml.read_bytes())


def test_storno_und_korrektur_kleinunternehmer(verz, con, kunde):
    stammdaten.speichern(con, {**STAMMDATEN, "kleinunternehmer": "1"})
    rid = service.anlegen(con, verz, formular(kunde))
    service.abschliessen(con, verz, rid)
    stammdaten.speichern(con, STAMMDATEN)            # Wechsel nach der Rechnung …
    sid = service.storno_anlegen(con, rid, "Fehler")
    xml, _ = service.abschliessen(con, verz, sid)
    assert service.laden(con, sid)["kleinunternehmer"] == 1      # … das Storno folgt dem Original
    assert wert(xml, "cac:TaxTotal/cac:TaxSubtotal/cac:TaxCategory/cbc:ID") == ["E"]
    offiziell_pruefen(xml.read_bytes())


def test_kunde_mit_storno_nach_frist_loeschbar(verz, con, kunde, rechnung):
    sid = service.storno_anlegen(con, rechnung, "Grund")
    service.abschliessen(con, verz, sid)
    kunden.archivieren(con, kunde, heute=date(2014, 1, 1))
    kunden.loeschen(con, verz, kunde)
    assert con.execute("SELECT count(*) FROM rechnungen").fetchone()[0] == 0


def test_migration_bestehender_datenbank(tmp_path):
    """Eine Datenbank im Stand vor Storno/Korrektur wird beim Start ergänzt; bestehende
    Rechnungen bleiben erhalten und können anschließend storniert werden."""
    from pathlib import Path
    pfad = tmp_path / "alt.db"
    alt = sqlite3.connect(pfad)
    alt.executescript((Path(__file__).parent / "vorlagen" / "schema_vor_storno.sql").read_text(encoding="utf-8"))
    alt.executescript("""
        INSERT INTO kunden (kundennummer, firma, plz, ort, email) VALUES ('K00001', 'Alt GmbH', '50667', 'Köln', 'a@b.de');
        INSERT INTO rechnungen (nummer, kunde_id, status, rechnungsdatum, leistung_von, faellig_am, summe_gesamt)
             VALUES ('RE-2025-0001', 1, 'entwurf', '2025-05-01', '2025-05-01', '2025-05-15', '119');
        INSERT INTO rechnung_positionen (rechnung_id, position, bezeichnung, einheit_code, menge, einzelpreis, ust_satz)
             VALUES (1, 1, 'Beratung', 'HUR', '1', '100', '19');
        UPDATE rechnungen SET status = 'offen' WHERE id = 1;""")
    alt.commit()
    alt.close()
    db.initialisieren(pfad)
    c = db.verbinden(pfad)
    assert tuple(c.execute("SELECT art, bezug_id, vorausbezahlt FROM rechnungen").fetchone()) == ("rechnung", None, "0")
    sid = service.storno_anlegen(c, 1, "Altbestand storniert", "2025-06-01")
    assert service.laden(c, sid)["bezug_nummer"] == "RE-2025-0001"
    with pytest.raises(sqlite3.IntegrityError):          # neuer Schreibschutz-Trigger greift
        c.execute("UPDATE rechnungen SET art = 'storno' WHERE id = 1")
    c.close()


# --- Weboberfläche ------------------------------------------------------------------------------

def test_web_storno_und_korrektur(client, verz, con, kunde, rechnung):
    seite = client.get(f"/rechnungen/{rechnung}").get_data(as_text=True)
    assert "Korrigieren" in seite and "Rechnung stornieren" in seite

    r = client.post(f"/rechnungen/{rechnung}/stornieren", data={"korrektur_grund": ""}, follow_redirects=True)
    assert "Grund der Stornierung" in r.get_data(as_text=True)
    r = client.post(f"/rechnungen/{rechnung}/stornieren", data={"korrektur_grund": "Kunde hat storniert"})
    sid = int(r.headers["Location"].rstrip("/").split("/")[-1])
    seite = client.get(f"/rechnungen/{sid}").get_data(as_text=True)
    assert "Entwurf Stornorechnung" in seite and f"RE-{JAHR}-0001" in seite and "Kunde hat storniert" in seite
    assert client.get(f"/rechnungen/{sid}/bearbeiten").status_code == 302
    assert client.get(f"/rechnungen/{sid}/pdf").data.startswith(b"%PDF")
    client.post(f"/rechnungen/{sid}/loeschen")

    r = client.post(f"/rechnungen/{rechnung}/korrigieren")
    assert r.headers["Location"].endswith("/bearbeiten")
    kid = int(r.headers["Location"].split("/")[-2])
    editor = client.get(f"/rechnungen/{kid}/bearbeiten").get_data(as_text=True)
    assert "Korrektur der Rechnung" in editor and "Grund der Korrektur" in editor
    r = client.post(f"/rechnungen/{kid}/bearbeiten", data=formular(kunde, "4", korrektur_grund="Stunden falsch",
                                                                    rechnungsdatum=HEUTE))
    assert r.status_code == 302
    r = client.post(f"/rechnungen/{kid}/abschliessen", follow_redirects=True)
    text = r.get_data(as_text=True)
    assert "_korrektur_zu_RE-" in text and "Zugehörige Belege" in text
    assert "_korrektur_zu_RE-" in client.get(f"/rechnungen/{kid}/xml").headers["Content-Disposition"]

    liste = client.get("/rechnungen/").get_data(as_text=True)
    assert "Korrektur</span> zu RE-" in liste and "Korrigiert" in liste
    assert f"KR-{JAHR}-0001" in client.get(f"/kunden/{kunde}").get_data(as_text=True)
    assert f"KR-{JAHR}-0001" in client.get("/rechnungen/?status=offen").get_data(as_text=True)
    assert f"RE-{JAHR}-0001" in client.get("/rechnungen/?status=korrigiert").get_data(as_text=True)
