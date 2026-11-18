"""Lizenz: Prüfung der Lizenznummer, Testversion ohne XRechnung, Nachholen nach dem Lizenzieren."""

import base64
from datetime import date, timedelta

import pytest

from app import create_app, db, lizenz
from app.rechnungen import service
from app.stammdaten import service as stammdaten
from tests.test_rechnungen import STAMMDATEN, beratung, formular, kunde, pos  # noqa: F401 (Fixtures)


@pytest.fixture(autouse=True)
def lizenzpflicht(monkeypatch):
    """Diese Tests prüfen den Betrieb mit Lizenzpflicht (Voll-/Testversion). Seit 1.1 ist sie
    vorerst abgeschaltet (lizenz.LIZENZPFLICHT = False) – hier wird sie eingeschaltet."""
    monkeypatch.setattr(lizenz, "LIZENZPFLICHT", True)

NAME = "Bäckerei Sonnenschein GmbH"


# ---------------------------------------------------------------------------
# Lizenznummern
# ---------------------------------------------------------------------------

def test_hinterlegter_schluessel_ist_gueltig():
    assert len(base64.b64decode(lizenz.OEFFENTLICHER_SCHLUESSEL)) == 32


def test_erzeugen_und_pruefen(lizenz_schluessel):
    nummer = lizenz.erzeugen(lizenz_schluessel, NAME, 7, ausgestellt=date(2026, 10, 27))
    assert nummer.startswith("XRK1-") and all(len(g) <= 5 for g in nummer.split("-"))
    l = lizenz.pruefen(NAME, nummer)
    assert (l.lizenznehmer, l.seriennummer, l.ausgestellt, l.gueltig_bis) == (NAME, 7, date(2026, 10, 27), None)
    assert l.typ_name == "Vollversion" and l.gueltig()


def test_eingabe_tolerant(lizenz_schluessel):
    nummer = lizenz.erzeugen(lizenz_schluessel, NAME, 1)
    # Kleinbuchstaben, Leerzeichen/Zeilenumbrüche, ohne Bindestriche, Name mit anderer Schreibweise
    eingabe = "  " + nummer.lower().replace("-", " ").replace("o", "0") + "\n"
    assert lizenz.pruefen("  bäckerei   SONNENSCHEIN gmbh ", eingabe).nummer == nummer


@pytest.mark.parametrize("name, aendern, meldung", [
    ("Andere Firma GmbH", None, "passen nicht zusammen"),
    (NAME, lambda n: n[:-3], "unvollständig"),
    (NAME, lambda n: n[:20] + ("A" if n[20] != "A" else "B") + n[21:], "passen nicht zusammen"),
    (NAME, lambda n: "ABC-" + n[5:], "XRK1"),
    ("", None, "Lizenznehmer"),
])
def test_ungueltige_lizenz(lizenz_schluessel, name, aendern, meldung):
    nummer = lizenz.erzeugen(lizenz_schluessel, NAME, 1)
    with pytest.raises(lizenz.LizenzFehler, match=meldung):
        lizenz.pruefen(name, aendern(nummer) if aendern else nummer)


def test_fremder_schluessel_wird_abgelehnt(lizenz_schluessel):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    nummer = lizenz.erzeugen(Ed25519PrivateKey.generate(), NAME, 1)
    with pytest.raises(lizenz.LizenzFehler):
        lizenz.pruefen(NAME, nummer)


def test_ablaufdatum(lizenz_schluessel):
    bis = date.today() + timedelta(days=10)
    nummer = lizenz.erzeugen(lizenz_schluessel, NAME, 3, gueltig_bis=bis)
    l = lizenz.pruefen(NAME, nummer)
    assert l.gueltig_bis == bis and not l.gueltig(bis + timedelta(days=1))
    with pytest.raises(lizenz.LizenzFehler, match="abgelaufen"):
        lizenz.pruefen(NAME, nummer, heute=bis + timedelta(days=1))


def test_lizenzdatei(tmp_path, lizenz_schluessel):
    assert lizenz.laden(tmp_path) == (None, "")
    l = lizenz.pruefen(NAME, lizenz.erzeugen(lizenz_schluessel, NAME, 5))
    lizenz.speichern(tmp_path, l)
    assert lizenz.laden(tmp_path)[0] == l
    # vom Installer geschriebene Datei (mit BOM)
    (tmp_path / lizenz.DATEI).write_text(f"[Lizenz]\nlizenznehmer={NAME}\nlizenznummer={l.nummer}\n",
                                         encoding="utf-8-sig")
    assert lizenz.laden(tmp_path)[0] == l
    (tmp_path / lizenz.DATEI).write_text("[Lizenz]\nlizenznehmer=Fremd\nlizenznummer=" + l.nummer, encoding="utf-8")
    geladen, fehler = lizenz.laden(tmp_path)
    assert geladen is None and "passen nicht" in fehler


@pytest.mark.parametrize("kodierung,bom", [("utf-16-le", b"\xff\xfe"), ("cp1252", b""), ("utf-8", b"")])
def test_lizenzdatei_mit_umlauten_jeder_kodierung(tmp_path, lizenz_schluessel, kodierung, bom):
    """Der Installer (NSIS) schreibt INI-Dateien als UTF-16 oder in der ANSI-Codepage –
    ein Lizenznehmer mit Umlauten muss trotzdem erkannt werden."""
    l = lizenz.pruefen(NAME, lizenz.erzeugen(lizenz_schluessel, NAME, 6))
    text = f"[Lizenz]\r\nlizenznehmer={NAME}\r\nlizenznummer={l.nummer}\r\n"
    (tmp_path / lizenz.DATEI).write_bytes(bom + text.encode(kodierung))
    assert lizenz.laden(tmp_path)[0] == l


def test_lizenzdatei_wird_als_utf16_geschrieben(tmp_path, lizenz_schluessel):
    l = lizenz.pruefen(NAME, lizenz.erzeugen(lizenz_schluessel, NAME, 7))
    lizenz.speichern(tmp_path, l)
    roh = (tmp_path / lizenz.DATEI).read_bytes()
    assert roh.startswith(b"\xff\xfe") and NAME in roh.decode("utf-16")
    assert lizenz.laden(tmp_path)[0] == l


def test_werkzeug(tmp_path, lizenz_schluessel, capsys):
    import sys
    from pathlib import Path

    from cryptography.hazmat.primitives import serialization as s

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "werkzeuge"))
    import lizenz_erzeugen

    privat = tmp_path / "privat.pem"
    privat.write_bytes(lizenz_schluessel.private_bytes(s.Encoding.PEM, s.PrivateFormat.PKCS8, s.NoEncryption()))
    assert lizenz_erzeugen.main(["erzeugen", "--privat", str(privat), "--name", NAME]) == 0
    assert lizenz_erzeugen.main(["erzeugen", "--privat", str(privat), "--name", "Zweite GmbH", "--bis", "2030-12-31"]) == 0
    zeilen = (tmp_path / "lizenzen.csv").read_text(encoding="utf-8").splitlines()
    assert len(zeilen) == 3 and zeilen[1].startswith(f"1;{NAME};") and zeilen[2].startswith("2;Zweite GmbH;")
    nummer = zeilen[2].split(";")[-1]
    capsys.readouterr()
    assert lizenz_erzeugen.main(["pruefen", "--name", "Zweite GmbH", "--nummer", nummer]) == 0
    assert "31.12.2030" in capsys.readouterr().out
    assert lizenz_erzeugen.main(["pruefen", "--name", NAME, "--nummer", nummer]) == 1


# ---------------------------------------------------------------------------
# Testversion in der Anwendung
# ---------------------------------------------------------------------------

@pytest.fixture
def app(tmp_path, lizenz_schluessel):
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
def client(app, con):
    return app.test_client()


def _entwurf(client, kunde, beratung):
    r = client.post("/rechnungen/neu", data=formular(kunde, pos(beratung)))
    assert r.status_code == 302
    return int(r.headers["Location"].rstrip("/").split("/")[-1])


def test_testversion_hinweise(client):
    seite = client.get("/kunden/").get_data(as_text=True)
    assert "testlizenz-band" in seite and "Testversion" in seite
    einst = client.get("/einstellungen/").get_data(as_text=True)
    assert "XRechnungen (XML) werden in der Testversion nicht erstellt" in einst


def test_testversion_ohne_xml(client, verz, con, kunde, beratung):
    rid = _entwurf(client, kunde, beratung)
    detail = client.get(f"/rechnungen/{rid}").get_data(as_text=True)
    assert "Abschließen &amp; PDF erstellen" in detail and "Testversion:" in detail

    text = client.post(f"/rechnungen/{rid}/abschliessen", follow_redirects=True).get_data(as_text=True)
    assert "XRechnung (XML) wurde nicht erstellt" in text and "noch nicht erstellt (Testversion)" in text
    r = service.laden(con, rid)
    dateien = service.dateien(verz, r)
    assert r["status"] == "offen" and r["nummer"].startswith("RE-") and r["xml_ausstehend"]
    assert dateien["pdf"].is_file() and not dateien["xml"].exists()
    assert not list(verz.rglob("*.xml"))
    assert client.get(f"/rechnungen/{rid}/xml").status_code == 404
    assert "XRechnung nicht erstellt (Testversion)" in str(service.verlauf(con, rid))

    # ohne Lizenz lässt sich die XRechnung auch nicht nachholen
    r = client.post(f"/rechnungen/{rid}/xrechnung-erstellen")
    assert r.status_code == 302 and "/einstellungen" in r.headers["Location"]
    assert not dateien["xml"].exists()


def test_lizenzieren_und_xml_nachholen(client, app, verz, con, kunde, beratung, lizenz_schluessel):
    rid = _entwurf(client, kunde, beratung)
    client.post(f"/rechnungen/{rid}/abschliessen")

    # falsche Lizenz über die Oberfläche
    nummer = lizenz.erzeugen(lizenz_schluessel, "Ene Mene Media GmbH", 9)
    r = client.post("/einstellungen/lizenz", data={"lizenznehmer": "Jemand anderes", "lizenznummer": nummer})
    assert r.status_code == 422 and "passen nicht zusammen" in r.get_data(as_text=True)
    assert app.config["LIZENZ"] is None

    r = client.post("/einstellungen/lizenz", data={"lizenznehmer": "Ene Mene Media GmbH", "lizenznummer": nummer},
                    follow_redirects=True)
    text = r.get_data(as_text=True)
    assert "Lizenziert für Ene Mene Media GmbH" in text and "testlizenz-band" not in text
    assert "lizenziert für Ene Mene Media GmbH" in text                       # Fußzeile
    assert lizenz.laden(verz)[0].seriennummer == 9

    detail = client.get(f"/rechnungen/{rid}").get_data(as_text=True)
    assert "XRechnung jetzt erstellen" in detail
    text = client.post(f"/rechnungen/{rid}/xrechnung-erstellen", follow_redirects=True).get_data(as_text=True)
    assert "XRechnung erstellt" in text
    x = client.get(f"/rechnungen/{rid}/xml")
    assert x.status_code == 200 and b"xrechnung_3.0" in x.data
    assert service.laden(con, rid)["xml_ausstehend"] is None
    # nur einmal
    with pytest.raises(service.RechnungsFehler):
        service.xml_nachholen(con, verz, rid)

    # Neue Rechnungen erhalten die XRechnung sofort
    rid2 = _entwurf(client, kunde, beratung)
    client.post(f"/rechnungen/{rid2}/abschliessen")
    assert service.dateien(verz, service.laden(con, rid2))["xml"].is_file()

    # Lizenz gilt auch nach einem Neustart
    assert create_app(verz).config["LIZENZ"].lizenznehmer == "Ene Mene Media GmbH"


def test_nachgeholte_xml_entspricht_der_sofort_erstellten(verz, con, kunde, beratung):
    """Für Rechnung und Storno: XML aus den eingefrorenen Angaben = XML beim Abschließen."""
    rid = service.anlegen(con, verz, formular(kunde, pos(beratung)))
    service.abschliessen(con, verz, rid)
    xml_pfad = service.dateien(verz, service.laden(con, rid))["xml"]
    original = xml_pfad.read_bytes()

    sid = service.storno_anlegen(con, rid, "Doppelt berechnet")
    service.abschliessen(con, verz, sid)
    storno_pfad = service.dateien(verz, service.laden(con, sid))["xml"]
    storno = storno_pfad.read_bytes()

    # Stammdaten und Kunde nachträglich ändern – die XRechnung darf sich nicht ändern
    stammdaten.speichern(con, {**STAMMDATEN, "firma": "Neue Firma GmbH", "iban": "DE02120300000000202051"})
    con.execute("UPDATE kunden SET firma = 'Umbenannt AG' WHERE id = ?", (kunde,))
    con.commit()
    for beleg, pfad, inhalt, bezahlt in ((rid, xml_pfad, original, False), (sid, storno_pfad, storno, False)):
        pfad.unlink()
        con.execute("UPDATE rechnungen SET xml_ausstehend = ? WHERE id = ?",
                    ('{"bezug_bezahlt": %s}' % str(bezahlt).lower(), beleg))
        con.commit()
        assert service.xml_nachholen(con, verz, beleg).read_bytes() == inhalt


def test_korrektur_in_testversion(verz, con, kunde, beratung):
    rid = service.anlegen(con, verz, formular(kunde, pos(beratung)))
    service.abschliessen(con, verz, rid, mit_xml=False)
    service.bezahlt_setzen(con, rid, date.today().isoformat())
    kid = service.korrektur_anlegen(con, rid)
    service.aendern(con, verz, kid, formular(kunde, pos(beratung, menge="3"), korrektur_grund="Stunden falsch"))
    service.abschliessen(con, verz, kid, mit_xml=False)
    for beleg in (rid, kid):
        assert service.xml_nachholen(con, verz, beleg).is_file()
    korrektur = service.dateien(verz, service.laden(con, kid))["xml"].read_text(encoding="utf-8")
    assert "<cbc:InvoiceTypeCode>384</cbc:InvoiceTypeCode>" in korrektur
    assert service.laden(con, rid)["nummer"] in korrektur and "PrepaidAmount" in korrektur


def test_abgelaufene_lizenz_ist_testversion(app, client, lizenzieren):
    lizenzieren(app, gueltig_bis=date.today() + timedelta(days=1))
    assert "testlizenz-band" not in client.get("/kunden/").get_data(as_text=True)
    object.__setattr__(app.config["LIZENZ"], "gueltig_bis", date.today() - timedelta(days=1))
    assert "testlizenz-band" in client.get("/kunden/").get_data(as_text=True)


def test_ohne_lizenzpflicht_immer_xrechnung(client, verz, con, kunde, beratung, monkeypatch):
    """Standard seit 1.1: keine Lizenzpflicht – kein Testversion-Hinweis, XRechnung wird erstellt."""
    monkeypatch.setattr(lizenz, "LIZENZPFLICHT", False)
    html = client.get("/kunden/").get_data(as_text=True)
    assert "testlizenz-band" not in html and "Testversion" not in html
    assert 'id="lizenz"' not in client.get("/einstellungen/").get_data(as_text=True)
    rid = _entwurf(client, kunde, beratung)
    assert "Abschließen &amp; XRechnung erstellen" in client.get(f"/rechnungen/{rid}").get_data(as_text=True)
    client.post(f"/rechnungen/{rid}/abschliessen")
    r = service.laden(con, rid)
    assert not r["xml_ausstehend"] and service.dateien(verz, r)["xml"].is_file()
