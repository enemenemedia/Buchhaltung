import sqlite3
from datetime import date, timedelta
from decimal import Decimal

import pytest

from app import berechnung, create_app, db
from app.angebote import service
from app.angebote.service import AngebotsFehler, ValidierungsFehler
from app.kunden import service as kunden
from app.leistungen import service as leistungen
from app.stammdaten import service as stammdaten

STAMMDATEN = {"firma": "Ene Mene Media GmbH", "strasse": "Hauptstr. 5", "plz": "50667", "ort": "Köln",
              "kontakt_name": "Erika Muster", "iban": "DE89370400440532013000", "zahlungsziel_tage": "14"}


@pytest.fixture
def app(tmp_path):
    return create_app(tmp_path)


@pytest.fixture
def con(app):
    c = db.verbinden(app.config["DATENBANK"])
    stammdaten.speichern(c, STAMMDATEN)
    yield c
    c.close()


@pytest.fixture
def client(app, con):
    return app.test_client()


@pytest.fixture
def kunde(app, con):
    ap = kunden.ansprechpartner_aus_formular([{"vorname": "Julia", "nachname": "Brandt", "anrede": "Frau"},
                                              {"vorname": "Tim", "nachname": "Kaya"}])
    return kunden.anlegen(con, app.config["DATENVERZEICHNIS"],
                          {"firma": "Bäckerei Sonnenschein GmbH", "strasse": "Lindenallee 14", "plz": "50667",
                           "ort": "Köln", "land": "DE"}, ap)


@pytest.fixture
def beratung(con):
    return leistungen.anlegen(con, {"bezeichnung": "Beratung", "art": "zeit", "einheit_code": "HUR",
                                    "preis": "95", "anzahl": "1", "beschreibung": "vor Ort"})


def formular(kid, *positionen, **kopf):
    f = {"kunde_id": str(kid), "ansprechpartner_id": "", "datum": "2026-10-25",
         "gueltig_bis": "2026-11-24", "betreff": "Website", "anmerkungen": ""}
    f.update(kopf)
    for i, p in enumerate(positionen):
        for k, v in p.items():
            f[f"pos-{i}-{k}"] = v
    return f


def pos_aus_leistung(lid, menge="2", preis="95"):
    return {"leistung_id": str(lid), "bezeichnung": "Beratung", "einheit_code": "HUR", "menge": menge,
            "einzelpreis": preis, "ust_satz": "19"}


FREI = {"bezeichnung": "Einrichtung Hosting", "einheit_code": "LS", "menge": "1", "einzelpreis": "149",
        "ust_satz": "19", "uebernehmen": "1"}


# --- Berechnung --------------------------------------------------------------

def test_berechnung_netto_mit_zwei_steuersaetzen():
    s = berechnung.berechnen([
        {"menge": "8", "einzelpreis": "95", "ust_satz": "19"},
        {"menge": "1000", "einzelpreis": "0.125", "ust_satz": "7"},
        {"menge": "1", "einzelpreis": "149", "ust_satz": "19"},
    ])
    assert (s.netto, s.ust, s.gesamt) == (Decimal("1034.00"), Decimal("181.46"), Decimal("1215.46"))
    assert [(g.satz, g.basis, g.betrag) for g in s.steuergruppen] == [
        (Decimal(19), Decimal("909.00"), Decimal("172.71")), (Decimal(7), Decimal("125.00"), Decimal("8.75"))]


def test_berechnung_brutto_positionen_ergeben_gesamtbetrag():
    pos = [{"menge": "1.5", "einzelpreis": "85", "ust_satz": s} for s in ("7", "19", "19")]
    s = berechnung.berechnen(pos, ausweis="brutto")
    assert sum(z.betrag_brutto for z in s.zeilen) == s.gesamt == Decimal("439.89")
    assert s.netto + s.ust == s.gesamt


def test_berechnung_kleinunternehmer_ohne_ust():
    s = berechnung.berechnen([{"menge": "2", "einzelpreis": "50", "ust_satz": "19"}], kleinunternehmer=True)
    assert (s.netto, s.ust, s.gesamt) == (Decimal("100.00"), Decimal(0), Decimal("100.00"))


# --- Anlegen ---------------------------------------------------------------------

def test_angebot_aus_leistungsliste(con, kunde, beratung):
    aid = service.anlegen(con, formular(kunde, pos_aus_leistung(beratung)))
    a = service.laden(con, aid)
    assert a["nummer"] == "AN-2026-0001" and a["status"] == "entwurf" and a["kunde_id"] == kunde
    assert (a["summe_netto"], a["summe_ust"], a["summe_gesamt"]) == ("190", "36.1", "226.1")
    p = service.positionen(con, aid)[0]
    assert (p["leistung_id"], p["leistungsnummer"], p["menge"]) == (beratung, "L0001", "2")
    assert service.naechste_nummer(con, 2026) == "AN-2026-0002"
    assert service.naechste_nummer(con, 2027) == "AN-2027-0001"


def test_freie_position_wird_ins_leistungsverzeichnis_uebernommen(con, kunde):
    aid = service.anlegen(con, formular(kunde, FREI))
    p = service.positionen(con, aid)[0]
    neu = leistungen.laden(con, p["leistung_id"])
    assert (neu["bezeichnung"], neu["art"], neu["einheit_code"], neu["preis"]) == ("Einrichtung Hosting", "pauschal", "LS", "149")
    assert p["leistungsnummer"] == neu["nummer"]
    assert "Aus Angebot AN-2026-0001 übernommen" in leistungen.verlauf(con, neu["id"])[0]["details"]["hinweise"]
    # Gleiche Leistung erneut frei eingegeben -> keine Dublette
    service.anlegen(con, formular(kunde, {**FREI, "bezeichnung": "einrichtung  HOSTING"}))
    assert len(leistungen.liste(con)) == 1


def test_freie_position_ohne_uebernahme(con, kunde):
    aid = service.anlegen(con, formular(kunde, {**FREI, "uebernehmen": ""}))
    assert service.positionen(con, aid)[0]["leistung_id"] is None
    assert leistungen.liste(con) == []


def test_angebot_kopiert_werte_der_leistung(con, kunde, beratung):
    aid = service.anlegen(con, formular(kunde, pos_aus_leistung(beratung, preis="80")))
    leistungen.aendern(con, beratung, {"bezeichnung": "Beratung", "art": "zeit", "einheit_code": "HUR", "preis": "120"})
    assert service.positionen(con, aid)[0]["einzelpreis"] == "80"


@pytest.mark.parametrize("aenderung, feld", [
    ({"kunde_id": ""}, "kunde_id"),
    ({"gueltig_bis": "2026-10-01"}, "gueltig_bis"),
    ({"datum": "kein datum"}, "datum"),
    ({"gueltig_bis": ""}, "gueltig_bis"),
])
def test_kopf_validierung(con, kunde, beratung, aenderung, feld):
    with pytest.raises(ValidierungsFehler) as e:
        service.anlegen(con, formular(kunde, pos_aus_leistung(beratung), **aenderung))
    assert feld in e.value.fehler


def test_positions_validierung(con, kunde):
    with pytest.raises(ValidierungsFehler) as e:
        service.anlegen(con, formular(kunde))
    assert "positionen" in e.value.fehler
    with pytest.raises(ValidierungsFehler) as e:
        service.anlegen(con, formular(kunde, {**FREI, "bezeichnung": "", "einzelpreis": "abc", "menge": "0"}))
    assert {"pos-0-bezeichnung", "pos-0-einzelpreis", "pos-0-menge"} <= set(e.value.fehler)
    assert leistungen.liste(con) == []


def test_ansprechpartner_muss_zum_kunden_gehoeren(app, con, kunde, beratung):
    anderer = kunden.anlegen(con, app.config["DATENVERZEICHNIS"], {"firma": "Andere", "land": "DE"},
                             kunden.ansprechpartner_aus_formular([{"nachname": "Fremd"}]))
    fremd = kunden.ansprechpartner(con, anderer)[0]["id"]
    with pytest.raises(ValidierungsFehler) as e:
        service.anlegen(con, formular(kunde, pos_aus_leistung(beratung), ansprechpartner_id=str(fremd)))
    assert "ansprechpartner_id" in e.value.fehler


def test_empfaenger_mit_ansprechpartner(con, kunde, beratung):
    ap = kunden.ansprechpartner(con, kunde)[0]
    aid = service.anlegen(con, formular(kunde, pos_aus_leistung(beratung), ansprechpartner_id=str(ap["id"])))
    assert service.laden(con, aid)["empfaenger"] == ("Bäckerei Sonnenschein GmbH\nFrau Julia Brandt\n"
                                                     "Lindenallee 14\n50667 Köln")


def test_archivierter_kunde_nicht_waehlbar(con, kunde, beratung):
    kunden.archivieren(con, kunde)
    with pytest.raises(ValidierungsFehler) as e:
        service.anlegen(con, formular(kunde, pos_aus_leistung(beratung)))
    assert "archiviert" in e.value.fehler["kunde_id"]


def test_preisausweis_aus_stammdaten(con, kunde, beratung):
    stammdaten.speichern(con, {**STAMMDATEN, "preisausweis": "brutto"})
    a = service.laden(con, service.anlegen(con, formular(kunde, pos_aus_leistung(beratung))))
    assert a["preisausweis"] == "brutto" and not a["kleinunternehmer"]
    stammdaten.speichern(con, {**STAMMDATEN, "kleinunternehmer": "1"})
    a = service.laden(con, service.anlegen(con, formular(kunde, pos_aus_leistung(beratung))))
    assert a["kleinunternehmer"] and a["summe_gesamt"] == "190" and a["summe_ust"] == "0"


# --- Ändern, Abschließen, Status --------------------------------------------------

def test_aendern_mit_protokoll(con, kunde, beratung):
    aid = service.anlegen(con, formular(kunde, pos_aus_leistung(beratung)))
    assert service.aendern(con, aid, formular(kunde, pos_aus_leistung(beratung))) is False
    assert service.aendern(con, aid, formular(kunde, pos_aus_leistung(beratung, menge="3"), FREI,
                                              gueltig_bis="2026-12-31"))
    e = service.verlauf(con, aid)[0]
    assert e["aktion"] == "geaendert"
    assert e["details"]["felder"]["Gültig bis"] == ["24.11.2026", "31.12.2026"]
    assert e["details"]["felder"]["Gesamtbetrag"] == ["226,10 €", "516,46 €"]
    assert any(h.startswith("Hinzugefügt: 1 Pauschale Einrichtung Hosting") for h in e["details"]["hinweise"])
    assert len(service.positionen(con, aid)) == 2


def test_abschliessen_legt_pdf_im_kundenordner_ab(app, con, kunde, beratung):
    verz = app.config["DATENVERZEICHNIS"]
    aid = service.anlegen(con, formular(kunde, pos_aus_leistung(beratung), anmerkungen="Hinweis <b>fett</b> & Co."))
    pfad = service.abschliessen(con, verz, aid)
    assert pfad == verz / "kunden" / "K00001" / "angebote" / "AN-2026-0001.pdf"
    assert pfad.read_bytes().startswith(b"%PDF")
    a = service.laden(con, aid)
    assert a["status"] == "offen" and a["pdf_datei"] == "kunden/K00001/angebote/AN-2026-0001.pdf"
    assert service.pdf_pfad(verz, a) == pfad
    with pytest.raises(AngebotsFehler):
        service.aendern(con, aid, formular(kunde, pos_aus_leistung(beratung)))
    with pytest.raises(AngebotsFehler):
        service.abschliessen(con, verz, aid)
    with pytest.raises(AngebotsFehler):
        service.loeschen(con, aid)


def test_abgeschlossenes_angebot_in_datenbank_geschuetzt(app, con, kunde, beratung):
    aid = service.anlegen(con, formular(kunde, pos_aus_leistung(beratung)))
    service.abschliessen(con, app.config["DATENVERZEICHNIS"], aid)
    for sql in ("UPDATE angebote SET summe_gesamt = '1' WHERE id = ?",
                "UPDATE angebote SET status = 'entwurf' WHERE id = ?",
                "DELETE FROM angebote WHERE id = ?",
                "UPDATE angebot_positionen SET einzelpreis = '1' WHERE angebot_id = ?",
                "DELETE FROM angebot_positionen WHERE angebot_id = ?"):
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(sql, (aid,))
    con.rollback()


def test_kunde_mit_angeboten_nach_frist_loeschbar(app, con, kunde, beratung):
    verz = app.config["DATENVERZEICHNIS"]
    aid = service.anlegen(con, formular(kunde, pos_aus_leistung(beratung)))
    service.abschliessen(con, verz, aid)
    kunden.archivieren(con, kunde, heute=date(2014, 1, 1))
    kunden.loeschen(con, verz, kunde)
    assert con.execute("SELECT count(*) FROM angebote").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM angebot_positionen").fetchone()[0] == 0


def test_abschliessen_braucht_stammdaten(app, con, kunde, beratung):
    aid = service.anlegen(con, formular(kunde, pos_aus_leistung(beratung)))
    con.execute("UPDATE stammdaten SET firma = ''")
    con.commit()
    with pytest.raises(AngebotsFehler, match="Stammdaten"):
        service.abschliessen(con, app.config["DATENVERZEICHNIS"], aid)
    assert service.laden(con, aid)["status"] == "entwurf"


def test_statusuebergaenge(app, con, kunde, beratung):
    aid = service.anlegen(con, formular(kunde, pos_aus_leistung(beratung)))
    with pytest.raises(AngebotsFehler):
        service.status_setzen(con, aid, "angenommen")
    service.abschliessen(con, app.config["DATENVERZEICHNIS"], aid)
    service.status_setzen(con, aid, "angenommen")
    service.status_setzen(con, aid, "offen")
    service.status_setzen(con, aid, "abgelehnt")
    with pytest.raises(AngebotsFehler):
        service.status_setzen(con, aid, "angenommen")
    aktionen = [e["aktion"] for e in service.verlauf(con, aid)]
    assert aktionen == ["status", "status", "status", "abgeschlossen", "angelegt"]


def test_abgelaufen(con, kunde, beratung):
    gestern = (date.today() - timedelta(days=1)).isoformat()
    aid = service.anlegen(con, formular(kunde, pos_aus_leistung(beratung), datum="2026-01-01", gueltig_bis=gestern))
    a = service.laden(con, aid)
    assert service.anzeige_status(a) == "abgelaufen"
    assert [x["id"] for x in service.liste(con, status="abgelaufen")] == [aid]
    assert service.liste(con, status="entwurf") == []


def test_kopie_und_loeschen(app, con, kunde, beratung):
    aid = service.anlegen(con, formular(kunde, pos_aus_leistung(beratung), FREI, betreff="Original"))
    service.abschliessen(con, app.config["DATENVERZEICHNIS"], aid)
    kopie = service.duplizieren(con, aid)
    k = service.laden(con, kopie)
    assert (k["status"], k["betreff"], k["datum"]) == ("entwurf", "Original", date.today().isoformat())
    assert [p["bezeichnung"] for p in service.positionen(con, kopie)] == ["Beratung", "Einrichtung Hosting"]
    assert len(leistungen.liste(con)) == 2
    service.loeschen(con, kopie)
    with pytest.raises(AngebotsFehler):
        service.laden(con, kopie)


def test_angebote_je_kunde(app, con, kunde, beratung):
    anderer = kunden.anlegen(con, app.config["DATENVERZEICHNIS"], {"firma": "Andere", "land": "DE"}, [])
    service.anlegen(con, formular(kunde, pos_aus_leistung(beratung)))
    service.anlegen(con, formular(anderer, pos_aus_leistung(beratung)))
    assert [a["nummer"] for a in service.fuer_kunde(con, kunde)] == ["AN-2026-0001"]


def test_migration_ergaenzt_spalten(tmp_path):
    pfad = tmp_path / "alt.db"
    alt = sqlite3.connect(pfad)
    alt.execute("CREATE TABLE stammdaten (id INTEGER PRIMARY KEY CHECK (id = 1), firma TEXT NOT NULL DEFAULT '')")
    alt.execute("INSERT INTO stammdaten (id, firma) VALUES (1, 'Alt GmbH')")
    alt.commit()
    alt.close()
    db.initialisieren(pfad)
    c = db.verbinden(pfad)
    z = c.execute("SELECT firma, preisausweis, angebot_gueltigkeit_tage FROM stammdaten").fetchone()
    assert tuple(z) == ("Alt GmbH", "netto", 30)


# --- Weboberfläche -----------------------------------------------------------------

def test_web_ablauf(client, app, con, kunde, beratung):
    seite = client.get(f"/angebote/neu?kunde={kunde}").get_data(as_text=True)
    assert "Bäckerei Sonnenschein" in seite and "L0001" in seite

    r = client.post("/angebote/neu", data=formular(kunde, {**FREI, "einzelpreis": "zehn"}))
    assert r.status_code == 422 and "keine gültige Zahl" in r.get_data(as_text=True)

    r = client.post("/angebote/neu", data=formular(kunde, pos_aus_leistung(beratung), FREI,
                                                    anmerkungen="Start ab KW 46"))
    assert r.status_code == 302
    detail = client.get(r.headers["Location"]).get_data(as_text=True)
    assert "AN-2026-0001" in detail and "Start ab KW 46" in detail and "Entwurf" in detail

    vorschau = client.get("/angebote/1/pdf")
    assert vorschau.mimetype == "application/pdf" and vorschau.data.startswith(b"%PDF")
    assert not (app.config["DATENVERZEICHNIS"] / "kunden" / "K00001" / "angebote" / "AN-2026-0001.pdf").exists()

    bearbeiten = client.get("/angebote/1/bearbeiten").get_data(as_text=True)
    assert 'value="95,00"' in bearbeiten and 'value="2"' in bearbeiten

    r = client.post("/angebote/1/abschliessen", follow_redirects=True)
    assert "PDF wurde im Kundenordner abgelegt" in r.get_data(as_text=True)
    assert client.get("/angebote/1/pdf").data.startswith(b"%PDF")
    assert client.get("/angebote/1/bearbeiten").status_code == 302

    kundenseite = client.get(f"/kunden/{kunde}").get_data(as_text=True)
    assert "AN-2026-0001" in kundenseite and "Offen" in kundenseite

    client.post("/angebote/1/status", data={"status": "angenommen"})
    assert "Angenommen" in client.get("/angebote/").get_data(as_text=True)
    r = client.post("/angebote/1/kopie")
    assert r.headers["Location"].endswith("/angebote/2/bearbeiten")
    assert client.post("/angebote/2/loeschen").status_code == 302
    assert client.get("/angebote/2").status_code == 404


def test_stammdaten_preisausweis_web(client, con):
    r = client.post("/stammdaten/", data={**STAMMDATEN, "preisausweis": "brutto", "angebot_gueltigkeit_tage": "45"},
                    follow_redirects=True)
    assert "Stammdaten gespeichert" in r.get_data(as_text=True)
    s = stammdaten.laden(con)
    assert (s["preisausweis"], s["angebot_gueltigkeit_tage"]) == ("brutto", 45)
    v = service.vorgaben(con)
    assert date.fromisoformat(v["gueltig_bis"]) - date.fromisoformat(v["datum"]) == timedelta(days=45)
