"""Netzwerkversion: Einrichtung, Anmeldung, Rollen, Protokoll mit Benutzer, nur lokales Netz, Zertifikat."""

import datetime as dt
import ssl

import pytest
from cryptography import x509

from app import create_app, db
from app.netzwerk import benutzer as bn
from app.netzwerk import erlaubt, im_lokalen_netz, netze_aus_text
from app.netzwerk import zertifikat

PW = "geheim-passwort-1"
LAN = {"REMOTE_ADDR": "192.168.1.20"}


@pytest.fixture
def app(tmp_path):
    return create_app(tmp_path, netzwerk=True)


def client(app, adresse=LAN):
    c = app.test_client()
    c.environ_base.update({"wsgi.url_scheme": "https", "HTTP_HOST": "buchhaltung.local", **adresse})
    return c


@pytest.fixture
def con(app):
    c = db.verbinden(app.config["DATENBANK"])
    yield c
    c.close()


def einrichten(app):
    c = client(app)
    c.get("/")                                     # erzeugt den Einrichtungscode
    code = app.config["EINRICHTUNGSCODE"]
    r = c.post("/einrichtung", data={"code": code.lower(), "name": "chefin", "anzeigename": "Maria",
                                     "passwort": PW, "wiederholung": PW})
    assert r.status_code == 302, r.get_data(as_text=True)
    return c


def anmelden(app, name, passwort=PW, adresse=LAN):
    c = client(app, adresse)
    r = c.post("/anmelden", data={"name": name, "passwort": passwort})
    assert r.status_code == 302, r.get_data(as_text=True)[:300]
    return c


def benutzer_anlegen(admin, name, rolle):
    r = admin.post("/benutzer/neu", data={"name": name, "anzeigename": name.title(), "rolle": rolle,
                                          "passwort": PW, "wiederholung": PW})
    assert r.status_code == 302, r.get_data(as_text=True)[:500]


def test_einzelplatz_unveraendert(tmp_path):
    a = create_app(tmp_path)
    c = a.test_client()
    assert c.get("/kunden/").status_code == 200
    assert "Abmelden" not in c.get("/kunden/").get_data(as_text=True)
    assert c.get("/benutzer/").status_code == 404


def test_einrichtung_mit_code(app, tmp_path):
    c = client(app)
    r = c.get("/kunden/")
    assert r.status_code == 302 and r.headers["Location"].endswith("/einrichtung")
    assert (tmp_path / "einrichtungscode.txt").exists()
    r = c.post("/einrichtung", data={"code": "FALSCH", "name": "x1", "passwort": PW, "wiederholung": PW})
    assert r.status_code == 400 and "Einrichtungscode ist falsch" in r.get_data(as_text=True)
    einrichten(app)
    assert not (tmp_path / "einrichtungscode.txt").exists()
    # nach der Einrichtung ist die Seite weg
    assert client(app).get("/einrichtung").headers["Location"].endswith("/anmelden")


def test_anmeldung_sperre_und_abmelden(app, con):
    einrichten(app)
    c = client(app)
    r = c.get("/belege/?jahr=2024")
    assert r.status_code == 302 and "/anmelden?weiter=/belege/" in r.headers["Location"]
    assert c.post("/anmelden", data={"name": "chefin", "passwort": "falsch"}).status_code == 401
    assert c.post("/anmelden", data={"name": "niemand", "passwort": PW}).status_code == 401
    r = c.post("/anmelden", data={"name": "CHEFIN", "passwort": PW, "weiter": "/belege/"})
    assert r.headers["Location"] == "/belege/"
    html = c.get("/belege/").get_data(as_text=True)
    assert "Maria" in html and "Abmelden" in html and "Benutzer" in html and "Datenordner" not in html
    c.post("/abmelden")
    assert c.get("/belege/").status_code == 302
    # fünf Fehlversuche sperren das Konto vorübergehend
    for _ in range(bn.MAX_FEHLVERSUCHE):
        c.post("/anmelden", data={"name": "chefin", "passwort": "falsch"})
    r = c.post("/anmelden", data={"name": "chefin", "passwort": PW})
    assert r.status_code == 401 and "gesperrt" in r.get_data(as_text=True)


def test_sitzungscookie_sicher(app):
    c = einrichten(app)
    r = c.post("/anmelden", data={"name": "chefin", "passwort": PW})
    cookie = r.headers["Set-Cookie"]
    assert "buchhaltung_sitzung=" in cookie and "Secure" in cookie and "HttpOnly" in cookie and "SameSite=Lax" in cookie
    assert r.headers["X-Frame-Options"] == "DENY"


def test_rollen(app, con):
    admin = einrichten(app)
    benutzer_anlegen(admin, "buero", "buchhaltung")
    benutzer_anlegen(admin, "pruefer", "lesen")
    beleg = {"art": "ausgabe", "belegdatum": "2024-03-05", "partner": "Lieferant", "beschreibung": "Papier",
             "kategorie": "buero", "ust_satz": "19", "netto": "100"}

    buero = anmelden(app, "buero")
    r = buero.post("/belege/neu", data=beleg)
    assert r.status_code == 302
    bid = int(r.headers["Location"].rstrip("/").split("/")[-1])
    assert buero.post(f"/belege/{bid}/festschreiben").status_code == 302
    assert buero.get("/stammdaten/").status_code == 200            # ansehen ja
    assert buero.post("/stammdaten/", data={}).status_code == 403   # ändern nein
    assert buero.get("/einstellungen/").status_code == 403
    assert buero.get("/benutzer/").status_code == 403
    assert "Benutzer</a>" not in buero.get("/belege/").get_data(as_text=True)

    leser = anmelden(app, "pruefer")
    assert leser.get(f"/belege/{bid}").status_code == 200
    assert leser.get("/belege/export.csv?jahr=2024").status_code == 200
    assert leser.get("/euer/2024.pdf").status_code == 200
    r = leser.post("/belege/neu", data=beleg)
    assert r.status_code == 403 and "Nur lesen" in r.get_data(as_text=True)
    assert leser.post(f"/belege/{bid}/stornieren", data={"grund": "x"}).status_code == 403
    assert "Nur lesen</strong>" in leser.get("/belege/").get_data(as_text=True)
    # eigenes Passwort ändern darf jeder
    neu = "ganz-neues-passwort"
    r = leser.post("/konto/passwort", data={"alt": PW, "neu": neu, "wiederholung": neu})
    assert r.status_code == 302
    assert leser.get("/belege/").status_code == 200                # diese Sitzung bleibt
    anmelden(app, "pruefer", neu)


def test_protokoll_mit_benutzer(app, con):
    admin = einrichten(app)
    benutzer_anlegen(admin, "buero", "buchhaltung")
    buero = anmelden(app, "buero")
    r = buero.post("/belege/neu", data={"art": "ausgabe", "belegdatum": "2024-03-05", "partner": "L",
                                        "beschreibung": "B", "kategorie": "buero", "ust_satz": "19", "netto": "1"})
    bid = int(r.headers["Location"].rstrip("/").split("/")[-1])
    z = con.execute("SELECT benutzer FROM aenderungsprotokoll WHERE bereich = 'beleg' AND objekt_id = ?",
                    (bid,)).fetchone()
    assert z["benutzer"] == "buero"
    assert "· buero" in buero.get(f"/belege/{bid}").get_data(as_text=True)
    html = admin.get("/benutzer/protokoll?benutzer=buero").get_data(as_text=True)
    assert "Beleg #%d" % bid in html and "chefin" not in html.split("<tbody>")[1]
    # Anlage der Benutzer selbst ist protokolliert
    assert con.execute("SELECT benutzer FROM aenderungsprotokoll WHERE bereich = 'benutzer' AND aktion = 'angelegt' "
                       "ORDER BY id DESC").fetchone()["benutzer"] == "chefin"


def test_deaktivieren_und_rollenwechsel_beenden_sitzung(app, con):
    admin = einrichten(app)
    benutzer_anlegen(admin, "buero", "buchhaltung")
    buero = anmelden(app, "buero")
    bid = con.execute("SELECT id FROM benutzer WHERE name = 'buero'").fetchone()[0]
    admin.post(f"/benutzer/{bid}", data={"anzeigename": "Büro", "rolle": "lesen", "aktiv": "1"})
    assert buero.get("/belege/").status_code == 302                    # Sitzung beendet
    admin.post(f"/benutzer/{bid}", data={"anzeigename": "Büro", "rolle": "lesen"})          # deaktiviert
    r = client(app).post("/anmelden", data={"name": "buero", "passwort": PW})
    assert r.status_code == 401 and "deaktiviert" in r.get_data(as_text=True)
    # letzter Administrator kann sich nicht selbst entmachten
    aid = con.execute("SELECT id FROM benutzer WHERE name = 'chefin'").fetchone()[0]
    admin.post(f"/benutzer/{aid}", data={"anzeigename": "", "rolle": "buchhaltung", "aktiv": "1"})
    assert con.execute("SELECT rolle FROM benutzer WHERE id = ?", (aid,)).fetchone()[0] == "admin"
    with pytest.raises(bn.BenutzerFehler):
        bn.anlegen(con, "chefin", "", "lesen", PW)                       # doppelt
    with pytest.raises(bn.BenutzerFehler):
        bn.anlegen(con, "neu", "", "lesen", "kurz")


def test_nur_lokales_netz(app):
    einrichten(app)
    assert client(app, {"REMOTE_ADDR": "8.8.8.8"}).get("/anmelden").status_code == 403
    assert client(app, {"REMOTE_ADDR": "127.0.0.1"}).get("/anmelden").status_code == 200
    assert im_lokalen_netz("10.1.2.3", None) and im_lokalen_netz("fd00::5", None)
    assert im_lokalen_netz("::ffff:192.168.0.9", None) and not im_lokalen_netz("::ffff:1.1.1.1", None)
    assert not im_lokalen_netz("93.184.216.34", None) and not im_lokalen_netz("unsinn", None)
    netze = netze_aus_text("192.168.1.0/24")
    assert im_lokalen_netz("192.168.1.7", netze) and not im_lokalen_netz("192.168.2.7", netze)
    # fremde Formulare
    c = anmelden(app, "chefin")
    assert c.post("/belege/neu", data={}, headers={"Origin": "https://boese.example"}).status_code == 403


def test_regeln():
    assert erlaubt("lesen", "GET", "belege.liste", "/belege/")
    assert not erlaubt("lesen", "POST", "belege.neu", "/belege/neu")
    assert erlaubt("lesen", "POST", "konto.passwort", "/konto/passwort")
    assert not erlaubt("buchhaltung", "GET", "einstellungen.seite", "/einstellungen/")
    assert not erlaubt("buchhaltung", "POST", "stammdaten.seite", "/stammdaten/")
    assert erlaubt("buchhaltung", "POST", "rechnungen.neu", "/rechnungen/neu")
    assert erlaubt("admin", "POST", "einstellungen.verschluesseln", "/einstellungen/verschluesseln")


def test_zertifikat(tmp_path):
    ordner = tmp_path / "zert"
    z = zertifikat.sicherstellen(ordner, ["nas.local", "192.168.1.10"])
    server = x509.load_pem_x509_certificate(z.server.read_bytes())
    ca = x509.load_pem_x509_certificate(z.ca.read_bytes())
    san = server.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert "nas.local" in san.get_values_for_type(x509.DNSName) and "localhost" in san.get_values_for_type(x509.DNSName)
    assert [str(i) for i in san.get_values_for_type(x509.IPAddress)][:1] == ["192.168.1.10"]
    assert server.issuer == ca.subject
    server.verify_directly_issued_by(ca)
    assert ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    assert server.not_valid_after_utc - server.not_valid_before_utc <= dt.timedelta(days=825)
    assert (z.schluessel.stat().st_mode & 0o077) == 0
    # passt alles: unverändert; neue Namen: neues Serverzertifikat mit derselben CA
    vorher, ca_vorher = z.server.read_bytes(), z.ca.read_bytes()
    assert zertifikat.sicherstellen(ordner, ["nas.local", "192.168.1.10"]).server.read_bytes() == vorher
    z2 = zertifikat.sicherstellen(ordner, ["buero-nas"])
    assert z2.server.read_bytes() != vorher and z2.ca.read_bytes() == ca_vorher
    ctx = ssl.create_default_context(cafile=str(z2.ca))
    ctx.load_cert_chain(z2.server, z2.schluessel)      # Schlüssel passt zum Zertifikat


def test_zertifikat_download(tmp_path):
    z = zertifikat.sicherstellen(tmp_path / "zert", ["nas"])
    a = create_app(tmp_path / "daten", netzwerk=True, einstellungen={"ZERTIFIKAT_CA": str(z.ca)})
    r = client(a).get("/zertifikat")                                   # ohne Anmeldung
    assert r.status_code == 200 and r.data.startswith(b"-----BEGIN CERTIFICATE-----")


def test_kennung_verraet_keinen_pfad(app):
    j = client(app).get("/system/kennung").get_json()
    assert j["netzwerk"] is True and "daten" not in j
