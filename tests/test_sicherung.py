"""Datensicherung: alle Daten und Dokumente, beliebiges Ziel, optional mit Passwort (AES-256)."""

import io
import json
import threading
import zipfile

import pytest
import pyzipper

from app import create_app, db
from app.sicherung import service


@pytest.fixture
def app(tmp_path):
    a = create_app(tmp_path / "daten")
    daten = a.config["DATENVERZEICHNIS"]
    (daten / "belege" / "2024" / "ab").mkdir(parents=True)
    (daten / "belege" / "2024" / "ab" / "rechnung.pdf").write_bytes(b"%PDF-1.4 beleg")
    (daten / "kunden" / "Muster GmbH").mkdir(parents=True)
    (daten / "kunden" / "Muster GmbH" / "RE-2024-0001.xml").write_text("<Invoice/>", encoding="utf-8")
    (daten / "protokoll").mkdir()
    (daten / "protokoll" / "start.log").write_text("x")
    (daten / "einrichtungscode.txt").write_text("GEHEIM")
    return a


@pytest.fixture
def client(app):
    return app.test_client()


def namen_im_zip(pfad, passwort=None):
    z = pyzipper.AESZipFile(pfad)
    if passwort:
        z.setpassword(passwort.encode())
    return z, set(z.namelist())


def test_sicherung_auf_laufwerk(app, client, tmp_path):
    ziel = tmp_path / "usb"
    html = client.get("/sicherung/").get_data(as_text=True)
    assert "noch nie" in html and "💾 Sichern" in html and "sicherung-knopf faellig" in html
    r = client.post("/sicherung/erstellen", data={"pfad": str(ziel / "Sicherungen")})
    assert r.status_code == 302, r.get_data(as_text=True)[:400]
    (datei,) = (ziel / "Sicherungen").glob("Buchhaltung-Sicherung_*.zip")
    assert not list((ziel / "Sicherungen").glob("*.teil"))
    with zipfile.ZipFile(datei) as z:                         # ohne Passwort: normale ZIP-Datei
        namen = set(z.namelist())
        assert z.testzip() is None
        manifest = json.loads(z.read("sicherung.json"))
    assert {"xrechnung.db", "belege/2024/ab/rechnung.pdf", "kunden/Muster GmbH/RE-2024-0001.xml",
            "LIESMICH.txt", "sicherung.json"} <= namen
    assert not {".secret_key", "einrichtungscode.txt", "protokoll/start.log"} & namen
    assert manifest["anzahl"] == len(manifest["dateien"]) and not manifest["zip_verschluesselt"]
    # vermerkt, Hinweis verschwindet, Ziel wird vorgeschlagen
    html = client.get("/sicherung/").get_data(as_text=True)
    assert "heute" in html and "sicherung-knopf faellig" not in html and str(ziel / "Sicherungen") in html
    con = db.verbinden(app.config["DATENBANK"])
    e = con.execute("SELECT * FROM aenderungsprotokoll WHERE bereich = 'sicherung'").fetchone()
    assert e["aktion"] == "erstellt" and datei.name in e["details"]


def test_mit_passwort_aes(app, client, tmp_path):
    r = client.post("/sicherung/erstellen", data={"pfad": str(tmp_path / "ziel"), "passwort": "sehr-geheim",
                                                  "wiederholung": "sehr-geheim"})
    assert r.status_code == 302
    (datei,) = (tmp_path / "ziel").glob("*.zip")
    with zipfile.ZipFile(datei) as z:                         # ohne Passwort nicht lesbar
        with pytest.raises(RuntimeError):
            z.read("sicherung.json")
    z, namen = namen_im_zip(datei, "falsch-falsch")
    with pytest.raises(RuntimeError):
        z.read("sicherung.json")
    z, namen = namen_im_zip(datei, "sehr-geheim")
    assert z.read("belege/2024/ab/rechnung.pdf") == b"%PDF-1.4 beleg"
    assert json.loads(z.read("sicherung.json"))["zip_verschluesselt"] is True
    # Datenbank aus der Sicherung ist eine vollständige SQLite-Datenbank
    db_kopie = tmp_path / "kopie.db"
    db_kopie.write_bytes(z.read("xrechnung.db"))
    con = db.verbinden(db_kopie)
    assert con.execute("SELECT COUNT(*) FROM tags").fetchone()[0] == 9


def test_fehler(app, client, tmp_path):
    daten = app.config["DATENVERZEICHNIS"]
    r = client.post("/sicherung/erstellen", data={"pfad": str(daten / "sicherung")})
    assert r.status_code == 400 and "nicht im Datenordner" in r.get_data(as_text=True)
    r = client.post("/sicherung/erstellen", data={"pfad": str(tmp_path / "z"), "passwort": "kurz", "wiederholung": "kurz"})
    assert r.status_code == 400 and "mindestens 8 Zeichen" in r.get_data(as_text=True)
    r = client.post("/sicherung/erstellen", data={"pfad": str(tmp_path / "z"), "passwort": "langes-pw-1",
                                                  "wiederholung": "langes-pw-2"})
    assert r.status_code == 400 and "stimmen nicht überein" in r.get_data(as_text=True)
    assert client.post("/sicherung/erstellen", data={"pfad": "relativ/pfad"}).status_code == 400
    assert client.post("/sicherung/erstellen", data={}).status_code == 400
    with pytest.raises(service.SicherungsFehler):
        service.zielordner("E:\\", "../../Windows")
    assert service.zielordner(str(tmp_path), "Sicherung/Buchhaltung") == tmp_path / "Sicherung" / "Buchhaltung"


def test_download(app, client):
    r = client.post("/sicherung/download", data={"passwort": "", "wiederholung": ""})
    assert r.status_code == 200 and r.mimetype == "application/zip"
    assert "Buchhaltung-Sicherung_" in r.headers["Content-Disposition"]
    with zipfile.ZipFile(io.BytesIO(r.data)) as z:
        assert "xrechnung.db" in z.namelist() and z.testzip() is None
    r.close()
    con = db.verbinden(app.config["DATENBANK"])
    assert '"ziel": "Download"' in con.execute(
        "SELECT details FROM aenderungsprotokoll WHERE bereich = 'sicherung'").fetchone()[0]


def test_konsistent_bei_gleichzeitigem_schreiben(app, tmp_path):
    """Während der Datenbank-Kopie wartet ein Schreiber; die Sicherung enthält einen konsistenten Stand."""
    daten, datenbank = app.config["DATENVERZEICHNIS"], app.config["DATENBANK"]
    stopp = threading.Event()

    def schreiber():
        con = db.verbinden(datenbank)
        i = 0
        while not stopp.is_set():
            con.execute("INSERT INTO tags (name) VALUES (?)", (f"T{i}",))
            con.commit()
            i += 1
        con.close()

    t = threading.Thread(target=schreiber)
    t.start()
    try:
        erg = service.erstellen(daten, datenbank, None, tmp_path / "ziel")
    finally:
        stopp.set()
        t.join()
    with zipfile.ZipFile(erg.pfad) as z:
        (tmp_path / "k.db").write_bytes(z.read("xrechnung.db"))
    con = db.verbinden(tmp_path / "k.db")
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_verschluesselte_datenbank(tmp_path):
    pytest.importorskip("sqlcipher3")
    a = create_app(tmp_path / "daten")
    c = a.test_client()
    pw = "db-passwort-123"
    r = c.post("/einstellungen/verschluesseln", data={"passwort": pw, "passwort2": pw, "verstanden": "1"})
    assert r.status_code == 302, r.get_data(as_text=True)[:300]
    assert a.config["DB_PASSWORT"] == pw
    assert c.post("/sicherung/erstellen", data={"pfad": str(tmp_path / "z")}).status_code == 302
    (datei,) = (tmp_path / "z").glob("*.zip")
    with zipfile.ZipFile(datei) as z:
        roh = z.read("xrechnung.db")
        assert not roh.startswith(b"SQLite format 3")             # bleibt verschlüsselt
        assert json.loads(z.read("sicherung.json"))["datenbank_verschluesselt"] is True
        assert "Datenbank-Passwort" in z.read("LIESMICH.txt").decode()


def test_netzwerk_nur_admin_und_docker_nur_download(tmp_path):
    from tests.test_netzwerk import PW, anmelden, benutzer_anlegen, einrichten
    a = create_app(tmp_path / "n", netzwerk=True)
    admin = einrichten(a)
    benutzer_anlegen(admin, "buero", "buchhaltung")
    assert anmelden(a, "buero").get("/sicherung/").status_code == 403
    assert "Datensicherung" in admin.get("/sicherung/").get_data(as_text=True)
    d = create_app(tmp_path / "d", netzwerk=True, einstellungen={"SICHERUNG_LAUFWERKE": False})
    admin = einrichten(d)
    html = admin.get("/sicherung/").get_data(as_text=True)
    assert "Sicherung herunterladen" in html and "auf Laufwerk speichern" not in html
    assert admin.post("/sicherung/erstellen", data={"pfad": str(tmp_path / "x")}).status_code == 302
    assert not (tmp_path / "x").exists()
