import os
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from app import create_app, db
from app.angebote import service as angebote
from app.kunden import service as kunden
from app.leistungen import service as leistungen
from app.rechnungen import service, xrechnung
from app.rechnungen.service import RechnungsFehler, ValidierungsFehler
from app.stammdaten import service as stammdaten

STAMMDATEN = {
    "firma": "Ene Mene Media GmbH", "inhaber": "Erika Muster", "strasse": "Hauptstr. 5", "plz": "50667", "ort": "Köln",
    "email": "info@example.de", "elektronische_adresse": "rechnung@example.de", "kontakt_name": "Erika Muster",
    "kontakt_telefon": "0221 123", "kontakt_email": "erika@example.de", "ust_id": "DE123456789",
    "registergericht": "Amtsgericht Köln", "registernummer": "HRB 12345", "bank": "Commerzbank",
    "iban": "DE89370400440532013000", "bic": "COBADEFFXXX", "zahlungsziel_tage": "14",
}
NS = {"cbc": xrechnung.NS_CBC, "cac": xrechnung.NS_CAC}
HEUTE = date.today().isoformat()

# --- Offizielle Prüfung (XSD + EN 16931 + XRechnung), falls eingerichtet -------------
_ARTEFAKTE = os.environ.get("XRECHNUNG_ARTEFAKTE")
_pruefer = None


def offiziell_pruefen(xml: bytes):
    """Prüft gegen die offiziellen Regeln; ohne eingerichtete Artefakte wird übersprungen."""
    global _pruefer
    if not _ARTEFAKTE:
        return None
    if _pruefer is None:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "werkzeuge"))
        from xrechnung_pruefen import Pruefer
        _pruefer = Pruefer(_ARTEFAKTE)
    erg = _pruefer.pruefen(xml)
    assert erg.gueltig, [f"{m.regel}: {m.text}" for m in erg.fehler]
    return erg


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
                                      "plz": "50667", "ort": "Köln", "land": "DE", "email": "buchhaltung@baeckerei.de"},
                          kunden.ansprechpartner_aus_formular([{"vorname": "Julia", "nachname": "Brandt",
                                                                "anrede": "Frau"}]))


@pytest.fixture
def beratung(con):
    return leistungen.anlegen(con, {"bezeichnung": "Beratung", "art": "zeit", "einheit_code": "HUR", "preis": "110"})


def formular(kid=None, *positionen, **kopf):
    f = {"kunde_modus": "bestand", "kunde_id": str(kid or ""), "ansprechpartner_id": "", "rechnungsdatum": HEUTE,
         "leistung_von": HEUTE, "leistung_bis": "", "zahlungsziel_tage": "14", "betreff": "", "anmerkungen": "",
         "kaeufer_referenz": "", "bestellnummer": "", "befreiung_art": "E", "befreiung_grund": ""}
    f.update(kopf)
    for i, p in enumerate(positionen):
        for k, v in p.items():
            f[f"pos-{i}-{k}"] = v
    return f


def pos(lid=None, menge="2", preis="110", ust="19", **mehr):
    p = {"leistung_id": str(lid or ""), "bezeichnung": "Beratung", "einheit_code": "HUR", "menge": menge,
         "einzelpreis": preis, "ust_satz": ust}
    p.update(mehr)
    return p


FREI = {"bezeichnung": "Druck Flyer", "einheit_code": "H87", "menge": "500", "einzelpreis": "0,125",
        "ust_satz": "7", "uebernehmen": "1"}

NEUKUNDE = {"kunde_modus": "neu", "neu_firma": "Stadtwerke Musterstadt", "neu_strasse": "Rathausplatz 1",
            "neu_plz": "01067", "neu_ort": "Dresden", "neu_land": "de", "neu_email": "eingang@stadtwerke.de",
            "neu_leitweg_id": "14612000-1234512345-06", "neu_ap_vorname": "Petra", "neu_ap_nachname": "Lang",
            "neu_ap_email": "p.lang@stadtwerke.de"}


def xml_wert(xml: bytes, pfad: str) -> list[str]:
    return [e.text for e in ET.fromstring(xml).findall(pfad, NS)]


# --- Drei Wege zur Rechnung ---------------------------------------------------------

def test_rechnung_aus_angenommenem_angebot(verz, con, kunde, beratung):
    f = {"kunde_id": str(kunde), "ansprechpartner_id": "", "datum": "2026-10-01", "gueltig_bis": "2026-10-31",
         "betreff": "Website", "anmerkungen": "", "pos-0-leistung_id": str(beratung), "pos-0-bezeichnung": "Beratung",
         "pos-0-einheit_code": "HUR", "pos-0-menge": "6", "pos-0-einzelpreis": "110", "pos-0-ust_satz": "19"}
    aid = angebote.anlegen(con, f)
    with pytest.raises(RechnungsFehler, match="angenommenen"):
        service.aus_angebot(con, aid)
    angebote.abschliessen(con, verz, aid)
    angebote.status_setzen(con, aid, "angenommen")
    rid = service.aus_angebot(con, aid)
    r = service.laden(con, rid)
    assert (r["status"], r["angebot_id"], r["kunde_id"], r["betreff"]) == ("entwurf", aid, kunde, "Website")
    assert "Angebot AN-2026-0001" in r["anmerkungen"]
    assert r["summe_gesamt"] == "785.4"
    assert [p["bezeichnung"] for p in service.positionen(con, rid)] == ["Beratung"]
    with pytest.raises(RechnungsFehler, match="bereits"):
        service.aus_angebot(con, aid)


def test_rechnung_aus_leistungen_fuer_kunden(verz, con, kunde, beratung):
    rid = service.anlegen(con, verz, formular(kunde, pos(beratung), FREI, bestellnummer="PO-1"))
    r = service.laden(con, rid)
    assert r["nummer"] is None and r["status"] == "entwurf"
    assert r["faellig_am"] == (date.today() + timedelta(days=14)).isoformat()
    # freie Position wurde ins Leistungsverzeichnis übernommen
    assert [l["bezeichnung"] for l in leistungen.liste(con)] == ["Beratung", "Druck Flyer"]


def test_rechnung_mit_neuem_kunden(verz, con):
    rid = service.anlegen(con, verz, {**formular(None, FREI), **NEUKUNDE})
    r = service.laden(con, rid)
    neu = kunden.laden(con, r["kunde_id"])
    assert (neu["kundennummer"], neu["firma"], neu["land"], neu["leitweg_id"]) == (
        "K00001", "Stadtwerke Musterstadt", "DE", "14612000-1234512345-06")
    assert kunden.ansprechpartner(con, neu["id"])[0]["nachname"] == "Lang"
    assert r["ansprechpartner_id"] == kunden.ansprechpartner(con, neu["id"])[0]["id"]
    assert (verz / "kunden" / "K00001" / "rechnungen").is_dir()
    assert any("in die Kundendatei übernommen" in h for h in service.verlauf(con, rid)[0]["details"]["hinweise"])


def test_neuer_kunde_wird_bei_fehlern_nicht_angelegt(verz, con):
    with pytest.raises(ValidierungsFehler) as e:
        service.anlegen(con, verz, {**formular(None, {**FREI, "einzelpreis": "x"}), **NEUKUNDE, "neu_plz": ""})
    assert {"neu_plz", "pos-0-einzelpreis"} <= set(e.value.fehler)
    assert kunden.liste(con, status="alle") == []


@pytest.mark.parametrize("aenderung, feld", [
    ({"kunde_id": ""}, "kunde_id"),
    ({"leistung_von": ""}, "leistung_von"),
    ({"leistung_bis": "2000-01-01"}, "leistung_bis"),
    ({"zahlungsziel_tage": "400"}, "zahlungsziel_tage"),
    ({"rechnungsdatum": "x"}, "rechnungsdatum"),
])
def test_validierung(verz, con, kunde, aenderung, feld):
    with pytest.raises(ValidierungsFehler) as e:
        service.anlegen(con, verz, formular(kunde, pos(), **aenderung))
    assert feld in e.value.fehler


# --- Prüfliste und Abschluss ----------------------------------------------------------

def test_pruefliste_meldet_fehlende_angaben(verz, con, beratung):
    k = kunden.anlegen(con, verz, {"firma": "Ohne Mail", "land": "DE"}, [])
    rid = service.anlegen(con, verz, formular(k, pos(beratung)))
    offen = [p.text for p in service.pruefliste(con, service.laden(con, rid)) if not p.ok]
    assert any("BT-49" in t for t in offen) and any("PLZ" in t for t in offen)
    with pytest.raises(RechnungsFehler, match="fehlen"):
        service.abschliessen(con, verz, rid)
    assert service.laden(con, rid)["status"] == "entwurf"


def test_abschliessen_legt_xml_und_pdf_gleichnamig_ab(verz, con, kunde, beratung):
    rid = service.anlegen(con, verz, formular(kunde, pos(beratung), FREI, betreff="Relaunch"))
    xml_pfad, pdf_pfad = service.abschliessen(con, verz, rid)
    erwartet = f"{HEUTE}_RE-{date.today().year}-0001_K00001"
    assert xml_pfad == verz / "kunden" / "K00001" / "rechnungen" / f"{erwartet}.xml"
    assert pdf_pfad == xml_pfad.with_suffix(".pdf")
    assert pdf_pfad.read_bytes().startswith(b"%PDF")
    r = service.laden(con, rid)
    assert (r["status"], r["nummer"], r["kaeufer_referenz"]) == ("offen", f"RE-{date.today().year}-0001", "K00001")
    xml = xml_pfad.read_bytes()
    assert xml_wert(xml, "cbc:ID") == [r["nummer"]]
    assert xml_wert(xml, "cac:LegalMonetaryTotal/cbc:PayableAmount") == ["328.68"]
    assert xml_wert(xml, "cac:AccountingCustomerParty/cac:Party/cbc:EndpointID") == ["buchhaltung@baeckerei.de"]
    offiziell_pruefen(xml)


def test_nummernkreis_fortlaufend_ohne_luecken(verz, con, kunde, beratung):
    ids = [service.anlegen(con, verz, formular(kunde, pos(beratung))) for _ in range(3)]
    service.loeschen(con, ids[1])                      # gelöschter Entwurf erzeugt keine Lücke
    service.abschliessen(con, verz, ids[2])
    service.abschliessen(con, verz, ids[0])
    jahr = date.today().year
    assert [service.laden(con, i)["nummer"] for i in (ids[2], ids[0])] == [f"RE-{jahr}-0001", f"RE-{jahr}-0002"]


def test_abgeschlossene_rechnung_unveraenderlich(verz, con, kunde, beratung):
    rid = service.anlegen(con, verz, formular(kunde, pos(beratung)))
    service.abschliessen(con, verz, rid)
    with pytest.raises(RechnungsFehler):
        service.aendern(con, verz, rid, formular(kunde, pos(beratung)))
    with pytest.raises(RechnungsFehler):
        service.loeschen(con, rid)
    for sql in ("UPDATE rechnungen SET summe_gesamt = '1' WHERE id = ?",
                "UPDATE rechnungen SET nummer = 'X' WHERE id = ?",
                "UPDATE rechnungen SET status = 'entwurf' WHERE id = ?",
                "DELETE FROM rechnungen WHERE id = ?",
                "UPDATE rechnung_positionen SET menge = '9' WHERE rechnung_id = ?",
                "DELETE FROM rechnung_positionen WHERE rechnung_id = ?"):
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(sql, (rid,))
    con.rollback()


def test_zahlung(verz, con, kunde, beratung):
    rid = service.anlegen(con, verz, formular(kunde, pos(beratung), zahlungsziel_tage="0",
                                              rechnungsdatum="2026-01-10", leistung_von="2026-01-05"))
    service.abschliessen(con, verz, rid)
    r = service.laden(con, rid)
    assert service.anzeige_status(r, date(2026, 2, 1)) == "ueberfaellig"
    with pytest.raises(RechnungsFehler):
        service.bezahlt_setzen(con, rid, "2025-12-31")
    service.bezahlt_setzen(con, rid, "2026-01-20")
    assert service.laden(con, rid)["bezahlt_am"] == "2026-01-20"
    service.zahlung_zuruecksetzen(con, rid)
    assert service.laden(con, rid)["status"] == "offen"


def test_kunde_nach_frist_mit_rechnungen_loeschbar(verz, con, kunde, beratung):
    rid = service.anlegen(con, verz, formular(kunde, pos(beratung)))
    service.abschliessen(con, verz, rid)
    kunden.archivieren(con, kunde, heute=date(2014, 1, 1))
    kunden.loeschen(con, verz, kunde)
    assert con.execute("SELECT count(*) FROM rechnungen").fetchone()[0] == 0


# --- XRechnung-Inhalte für verschiedene Steuerfälle --------------------------------------

def test_xrechnung_leistungszeitraum_und_leitweg(verz, con):
    rid = service.anlegen(con, verz, {**formular(None, pos(), leistung_von="2026-10-01", leistung_bis="2026-10-24",
                                                 bestellnummer="PO-4711", kaeufer_referenz="14612000-1234512345-06"),
                                      **NEUKUNDE})
    xml, _ = service.abschliessen(con, verz, rid)
    daten = xml.read_bytes()
    assert xml_wert(daten, "cac:InvoicePeriod/cbc:StartDate") == ["2026-10-01"]
    assert xml_wert(daten, "cbc:BuyerReference") == ["14612000-1234512345-06"]
    assert xml_wert(daten, "cac:OrderReference/cbc:ID") == ["PO-4711"]
    assert xml_wert(daten, "cac:Delivery/cbc:ActualDeliveryDate") == []
    offiziell_pruefen(daten)


def test_xrechnung_kleinunternehmer(verz, con, kunde, beratung):
    stammdaten.speichern(con, {**STAMMDATEN, "kleinunternehmer": "1", "ust_id": "", "steuernummer": "214/5678/1234"})
    rid = service.anlegen(con, verz, formular(kunde, pos(beratung)))
    xml, pdf = service.abschliessen(con, verz, rid)
    daten = xml.read_bytes()
    assert xml_wert(daten, "cac:TaxTotal/cac:TaxSubtotal/cac:TaxCategory/cbc:ID") == ["E"]
    assert xml_wert(daten, "cac:TaxTotal/cbc:TaxAmount") == ["0.00"]
    assert "§ 19 UStG" in xml_wert(daten, "cac:TaxTotal/cac:TaxSubtotal/cac:TaxCategory/cbc:TaxExemptionReason")[0]
    assert service.laden(con, rid)["summe_gesamt"] == "220"
    offiziell_pruefen(daten)


def test_xrechnung_reverse_charge(verz, con, beratung):
    k = kunden.anlegen(con, verz, {"firma": "Alpen Design OG", "strasse": "Maria-Theresien-Str. 1", "plz": "6020",
                                   "ort": "Innsbruck", "land": "AT", "email": "office@alpen.at", "ust_id": "ATU12345678"}, [])
    rid = service.anlegen(con, verz, formular(k, pos(beratung, ust="0"), befreiung_art="AE"))
    xml, _ = service.abschliessen(con, verz, rid)
    daten = xml.read_bytes()
    assert xml_wert(daten, "cac:TaxTotal/cac:TaxSubtotal/cac:TaxCategory/cbc:ID") == ["AE"]
    assert xml_wert(daten, "cac:TaxTotal/cac:TaxSubtotal/cac:TaxCategory/cbc:TaxExemptionReasonCode") == ["VATEX-EU-AE"]
    offiziell_pruefen(daten)


def test_reverse_charge_braucht_ust_id_des_kunden(verz, con, kunde, beratung):
    rid = service.anlegen(con, verz, formular(kunde, pos(beratung, ust="0"), befreiung_art="AE"))
    offen = [p.text for p in service.pruefliste(con, service.laden(con, rid)) if not p.ok]
    assert any("Reverse Charge" in t for t in offen)


def test_xrechnung_gemischte_steuersaetze(verz, con, kunde, beratung):
    rid = service.anlegen(con, verz, formular(kunde, pos(beratung, menge="6"), FREI,
                                              pos(None, menge="1", preis="50", ust="0", bezeichnung="Porto"),
                                              befreiung_grund="Steuerfrei nach § 4 Nr. 11b UStG",
                                              anmerkungen="Vielen Dank & bis bald <3"))
    xml, _ = service.abschliessen(con, verz, rid)
    daten = xml.read_bytes()
    assert sorted(xml_wert(daten, "cac:TaxTotal/cac:TaxSubtotal/cac:TaxCategory/cbc:Percent")) == ["0", "19", "7"]
    assert xml_wert(daten, "cac:LegalMonetaryTotal/cbc:TaxExclusiveAmount") == ["772.50"]
    offiziell_pruefen(daten)


# --- Weboberfläche ------------------------------------------------------------------------

def test_web_ablauf(client, verz, con, kunde, beratung):
    assert "Aus der Kundendatei" in client.get("/rechnungen/neu").get_data(as_text=True)
    r = client.post("/rechnungen/neu", data=formular(kunde, {**FREI, "menge": "0"}))
    assert r.status_code == 422 and "größer als 0" in r.get_data(as_text=True)

    r = client.post("/rechnungen/neu", data=formular(kunde, pos(beratung)))
    assert r.status_code == 302
    detail = client.get(r.headers["Location"]).get_data(as_text=True)
    assert "Bereit für die XRechnung" in detail and "Entwurf Rechnung #1" in detail
    assert client.get("/rechnungen/1/pdf").data.startswith(b"%PDF")
    assert client.get("/rechnungen/1/xml").status_code == 404
    assert 'value="110,00"' in client.get("/rechnungen/1/bearbeiten").get_data(as_text=True)

    r = client.post("/rechnungen/1/abschliessen", follow_redirects=True)
    assert "im Rechnungsordner abgelegt" in r.get_data(as_text=True)
    x = client.get("/rechnungen/1/xml")
    assert x.mimetype == "application/xml" and b"xrechnung_3.0" in x.data
    assert "attachment" in x.headers["Content-Disposition"] and "_K00001.xml" in x.headers["Content-Disposition"]
    assert "RE-" in client.get(f"/kunden/{kunde}").get_data(as_text=True)

    client.post("/rechnungen/1/bezahlt", data={"bezahlt_am": HEUTE})
    assert "Bezahlt" in client.get("/rechnungen/").get_data(as_text=True)


def test_web_neukunde_und_angebot(client, verz, con, kunde, beratung):
    r = client.post("/rechnungen/neu", data={**formular(None, FREI), **NEUKUNDE})
    assert r.status_code == 302
    assert "Stadtwerke Musterstadt" in client.get("/kunden/").get_data(as_text=True)

    f = {"kunde_id": str(kunde), "datum": HEUTE, "gueltig_bis": HEUTE, "pos-0-bezeichnung": "Beratung",
         "pos-0-einheit_code": "HUR", "pos-0-menge": "1", "pos-0-einzelpreis": "100", "pos-0-ust_satz": "19"}
    aid = angebote.anlegen(con, f)
    angebote.abschliessen(con, verz, aid)
    angebote.status_setzen(con, aid, "angenommen")
    assert "Rechnung erstellen" in client.get(f"/angebote/{aid}").get_data(as_text=True)
    r = client.post(f"/rechnungen/aus-angebot/{aid}")
    assert r.status_code == 302 and "/rechnungen/" in r.headers["Location"]
    assert "Rechnungsentwurf #2" in client.get(f"/angebote/{aid}").get_data(as_text=True)
