"""Verschlüsselung der Datenbank mit einem vom Nutzer vergebenen Passwort."""

import sqlite3

import pytest

from app import create_app, db
from app.kunden import service as kunden

pytestmark = pytest.mark.skipif(not db.VERSCHLUESSELUNG_VERFUEGBAR, reason="sqlcipher3 nicht installiert")

PW = "Sicher'es Passwort 1"


@pytest.fixture
def app(tmp_path):
    a = create_app(tmp_path)
    con = db.verbinden(a.config["DATENBANK"])
    kunden.anlegen(con, tmp_path, {"firma": "Bäckerei Sonnenschein GmbH", "plz": "50667", "ort": "Köln", "land": "DE"},
                   kunden.ansprechpartner_aus_formular([]))
    con.close()
    return a


@pytest.fixture
def client(app):
    return app.test_client()


def _verschluesseln(client, pw=PW, pw2=None, verstanden="1"):
    return client.post("/einstellungen/verschluesseln",
                       data={"passwort": pw, "passwort2": pw if pw2 is None else pw2, "verstanden": verstanden})


def test_eingaben_werden_geprueft(client, app):
    assert _verschluesseln(client, "kurz").status_code == 422
    r = _verschluesseln(client, pw2="anders123")
    assert r.status_code == 422 and "stimmen nicht überein" in r.get_data(as_text=True)
    r = _verschluesseln(client, verstanden="")
    assert r.status_code == 422 and "sicher notiert" in r.get_data(as_text=True)
    assert not db.ist_verschluesselt(app.config["DATENBANK"])


def test_verschluesseln_entsperren_sperren(client, app, tmp_path):
    pfad = app.config["DATENBANK"]
    assert not db.ist_verschluesselt(pfad)
    r = _verschluesseln(client)
    assert r.status_code == 302
    assert db.ist_verschluesselt(pfad) and app.config["DB_PASSWORT"] == PW
    assert not list(tmp_path.glob("*.umschreiben"))
    # Inhalt ist nicht mehr lesbar – auch nicht mit normalem SQLite
    assert b"Sonnenschein" not in pfad.read_bytes()
    with pytest.raises(sqlite3.DatabaseError):
        sqlite3.connect(pfad).execute("SELECT * FROM kunden").fetchall()
    # weiterarbeiten ohne Neustart
    seite = client.get("/kunden/").get_data(as_text=True)
    assert "Sonnenschein" in seite and "Sperren" in seite
    assert "Verschlüsselt (AES-256)" in client.get("/einstellungen/").get_data(as_text=True)

    # Neustart: gesperrt bis zur Eingabe des Passworts
    neu = create_app(tmp_path).test_client()
    r = neu.get("/kunden/?q=Sonne")
    assert r.status_code == 302 and "/einstellungen/entsperren" in r.headers["Location"]
    assert neu.get("/system/kennung").status_code == 200           # Starter erkennt das Programm weiterhin
    r = neu.post("/einstellungen/entsperren", data={"passwort": "falsch!!", "weiter": "/kunden/"})
    assert r.status_code == 401 and "Passwort ist falsch" in r.get_data(as_text=True)
    r = neu.post("/einstellungen/entsperren", data={"passwort": PW, "weiter": "/kunden/?q=Sonne"})
    assert r.status_code == 302 and r.headers["Location"] == "/kunden/?q=Sonne"
    assert "Sonnenschein" in neu.get("/kunden/").get_data(as_text=True)

    # Sperren
    assert neu.post("/einstellungen/sperren").status_code == 302
    assert neu.get("/kunden/").status_code == 302


def test_umleitung_nur_innerhalb_der_anwendung(client, app, tmp_path):
    _verschluesseln(client)
    neu = create_app(tmp_path).test_client()
    r = neu.post("/einstellungen/entsperren", data={"passwort": PW, "weiter": "//boese.example/x"})
    assert r.headers["Location"] == "/"


def test_schutz_bleibt_erhalten(client, app):
    """Trigger und Fremdschlüssel gelten auch in der verschlüsselten Datenbank."""
    _verschluesseln(client)
    con = db.verbinden(app.config["DATENBANK"], PW)
    try:
        with pytest.raises(Exception, match="Aufbewahrungsfrist|archiviert|gelöscht"):
            con.execute("DELETE FROM kunden")
        assert con.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert con.execute("SELECT count(*) FROM kunden").fetchone()[0] == 1
    finally:
        con.close()
    with pytest.raises(db.PasswortFalsch):
        db.verbinden(app.config["DATENBANK"], "falsch!!")


def test_passwort_aendern_und_aufheben(client, app, tmp_path):
    _verschluesseln(client)
    r = client.post("/einstellungen/passwort-aendern", data={"alt": "falsch!!", "passwort": "Neu12345", "passwort2": "Neu12345"})
    assert r.status_code == 422
    r = client.post("/einstellungen/passwort-aendern", data={"alt": PW, "passwort": "Neu12345", "passwort2": "Neu12345"})
    assert r.status_code == 302 and app.config["DB_PASSWORT"] == "Neu12345"
    with pytest.raises(db.PasswortFalsch):
        db.verbinden(app.config["DATENBANK"], PW)
    db.verbinden(app.config["DATENBANK"], "Neu12345").close()
    assert "Sonnenschein" in client.get("/kunden/").get_data(as_text=True)

    assert client.post("/einstellungen/entschluesseln", data={"passwort": PW}).status_code == 422
    r = client.post("/einstellungen/entschluesseln", data={"passwort": "Neu12345"})
    assert r.status_code == 302 and app.config["DB_PASSWORT"] is None
    assert not db.ist_verschluesselt(app.config["DATENBANK"])
    assert sqlite3.connect(app.config["DATENBANK"]).execute("SELECT firma FROM kunden").fetchone()[0].startswith("Bäckerei")
    assert create_app(tmp_path).config["GESPERRT"] is False


def test_fremde_webseite_wird_abgewiesen(client, app):
    r = client.post("/einstellungen/verschluesseln", headers={"Origin": "http://boese.example"},
                    data={"passwort": PW, "passwort2": PW, "verstanden": "1"})
    assert r.status_code == 403 and not db.ist_verschluesselt(app.config["DATENBANK"])


def test_migration_beim_entsperren(tmp_path):
    """Eine ältere verschlüsselte Datenbank erhält beim Entsperren die neuen Spalten."""
    pfad = tmp_path / "xrechnung.db"
    db.initialisieren(pfad)
    con = sqlite3.connect(pfad)
    con.execute("ALTER TABLE rechnungen DROP COLUMN xml_ausstehend")
    con.commit()
    con.close()
    db.verschluesseln(pfad, PW)
    client = create_app(tmp_path).test_client()
    client.post("/einstellungen/entsperren", data={"passwort": PW})
    con = db.verbinden(pfad, PW)
    assert "xml_ausstehend" in {z["name"] for z in con.execute("PRAGMA table_info(rechnungen)")}
    con.close()
