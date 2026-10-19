import sqlite3
from datetime import date

import pytest

from app import create_app, db
from app.kunden import service
from app.kunden.service import KundenFehler, ValidierungsFehler


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


def kunde_anlegen(con, app, **extra):
    daten = {"firma": "Muster GmbH", "strasse": "Hauptstr. 1", "plz": "10115", "ort": "Berlin", "land": "DE"}
    daten.update(extra)
    ap = service.ansprechpartner_aus_formular([
        {"vorname": "Erika", "nachname": "Muster", "email": "erika@muster.de", "telefon": "030 123"},
        {"vorname": "Max", "nachname": "Beispiel", "email": "max@muster.de", "telefon": "030 456"},
    ])
    return service.anlegen(con, app.config["DATENVERZEICHNIS"], daten, ap)


# --- Aufbewahrungsfrist ----------------------------------------------------

@pytest.mark.parametrize("archiv, erwartet", [
    (date(2026, 10, 19), date(2037, 1, 1)),
    (date(2026, 1, 1), date(2037, 1, 1)),
    (date(2026, 12, 31), date(2037, 1, 1)),
])
def test_loeschbar_ab(archiv, erwartet):
    assert service.loeschbar_ab(archiv) == erwartet


def test_frist_richtet_sich_nach_letztem_beleg():
    assert service.loeschbar_ab(date(2026, 3, 1), [date(2028, 5, 5)]) == date(2039, 1, 1)


# --- Anlegen & Ändern ------------------------------------------------------

def test_anlegen_vergibt_nummer_und_ordner(app, con):
    kid = kunde_anlegen(con, app)
    k = service.laden(con, kid)
    assert k["kundennummer"] == "K00001"
    aps = service.ansprechpartner(con, kid)
    assert len(aps) == 2
    assert sum(a["ist_hauptkontakt"] for a in aps) == 1
    ordner = app.config["DATENVERZEICHNIS"] / "kunden" / "K00001"
    assert all((ordner / u).is_dir() for u in service.KUNDEN_UNTERORDNER)
    assert service.protokoll(con, kid)[0]["aktion"] == "angelegt"


def test_validierung(app, con):
    with pytest.raises(ValidierungsFehler) as e:
        service.anlegen(con, app.config["DATENVERZEICHNIS"],
                        {"firma": "", "land": "DE", "plz": "123", "email": "x"}, [])
    assert {"firma", "plz", "email"} <= set(e.value.fehler)


def test_aendern_protokolliert_felder_und_ansprechpartner(app, con):
    kid = kunde_anlegen(con, app)
    aps = [dict(a) for a in service.ansprechpartner(con, kid)]
    aps[0]["telefon"] = "030 999"
    aps = aps[:1] + [{"vorname": "Neu", "nachname": "Kontakt"}]   # zweiten entfernen, dritten hinzufügen
    neu = service.ansprechpartner_aus_formular(aps)
    daten = dict(service.laden(con, kid))
    daten["ort"] = "Potsdam"
    daten["plz"] = "14467"
    assert service.aendern(con, kid, daten, neu)

    eintrag = service.protokoll(con, kid)[0]
    assert eintrag["aktion"] == "geaendert"
    assert eintrag["details"]["felder"]["Ort"] == ["Berlin", "Potsdam"]
    texte = " | ".join(eintrag["details"]["ansprechpartner"])
    assert "hinzugefügt: Neu Kontakt" in texte and "entfernt: Max Beispiel" in texte and "telefon" in texte
    assert len(service.ansprechpartner(con, kid)) == 2


def test_aendern_ohne_aenderung(app, con):
    kid = kunde_anlegen(con, app)
    aps = service.ansprechpartner_aus_formular([dict(a) for a in service.ansprechpartner(con, kid)])
    assert service.aendern(con, kid, dict(service.laden(con, kid)), aps) is False


# --- Archivieren & Löschen -------------------------------------------------

def test_archivierte_kunden_sind_schreibgeschuetzt(app, con):
    kid = kunde_anlegen(con, app)
    service.archivieren(con, kid)
    with pytest.raises(KundenFehler):
        service.aendern(con, kid, {"firma": "X", "land": "DE"}, [])
    # Auch direkter SQL-Zugriff wird von der Datenbank abgewiesen.
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("UPDATE kunden SET firma = 'X' WHERE id = ?", (kid,))
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO ansprechpartner (kunde_id, nachname) VALUES (?, 'X')", (kid,))


def test_reaktivieren(app, con):
    kid = kunde_anlegen(con, app)
    service.archivieren(con, kid)
    service.reaktivieren(con, kid)
    k = service.laden(con, kid)
    assert k["archiviert_am"] is None and k["loeschbar_ab"] is None


def test_aktive_kunden_nicht_loeschbar(app, con):
    kid = kunde_anlegen(con, app)
    with pytest.raises(KundenFehler, match="Nur archivierte"):
        service.loeschen(con, app.config["DATENVERZEICHNIS"], kid)
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("DELETE FROM kunden WHERE id = ?", (kid,))


def test_loeschen_innerhalb_der_frist_gesperrt(app, con):
    kid = kunde_anlegen(con, app)
    service.archivieren(con, kid)
    with pytest.raises(KundenFehler, match="Aufbewahrungsfrist"):
        service.loeschen(con, app.config["DATENVERZEICHNIS"], kid)
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("DELETE FROM kunden WHERE id = ?", (kid,))


def test_loeschen_nach_ablauf_der_frist(app, con):
    kid = kunde_anlegen(con, app)
    service.archivieren(con, kid, heute=date(2014, 6, 30))   # löschbar ab 01.01.2025
    service.loeschen(con, app.config["DATENVERZEICHNIS"], kid)
    assert con.execute("SELECT count(*) FROM kunden").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM ansprechpartner").fetchone()[0] == 0
    assert not (app.config["DATENVERZEICHNIS"] / "kunden" / "K00001").exists()
    assert service.protokoll(con, kid)[0]["aktion"] == "geloescht"


# --- Weboberfläche ---------------------------------------------------------

def test_web_ablauf(client, app):
    r = client.post("/kunden/neu", data={
        "firma": "Web AG", "plz": "80331", "ort": "München", "land": "de",
        "ap-0-vorname": "Anna", "ap-0-nachname": "Alt", "ap-0-email": "anna@web.de",
        "ap-3-nachname": "Zweit", "ap-3-telefon": "089 1",
        "hauptkontakt": "3",
    })
    assert r.status_code == 302
    detail = client.get(r.headers["Location"]).get_data(as_text=True)
    assert "Web AG" in detail and "Anna" in detail and "Zweit" in detail

    with app.app_context():
        con = db.get_db()
        aps = service.ansprechpartner(con, 1)
        assert aps[0]["nachname"] == "Zweit" and aps[0]["ist_hauptkontakt"]
        assert service.laden(con, 1)["land"] == "DE"

    assert client.post("/kunden/1/archivieren").status_code == 302
    assert "Web AG" in client.get("/kunden/?status=archiviert").get_data(as_text=True)
    assert "Web AG" not in client.get("/kunden/").get_data(as_text=True)
    r = client.post("/kunden/1/loeschen", data={"bestaetigung": "K00001"}, follow_redirects=True)
    assert "Aufbewahrungsfrist" in r.get_data(as_text=True)


def test_web_validierung(client):
    r = client.post("/kunden/neu", data={"firma": "", "land": "DE", "ap-0-vorname": "Ohne"})
    assert r.status_code == 422
    assert "Bitte den Firmennamen angeben" in r.get_data(as_text=True)
