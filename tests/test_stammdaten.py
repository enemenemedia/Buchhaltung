import io

import pytest
from PIL import Image

from app import create_app, db
from app.stammdaten import service
from app.stammdaten.service import StammdatenFehler, ValidierungsFehler


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


VOLLSTAENDIG = {
    "firma": "Ene Mene Media GmbH", "inhaber": "Erika Muster",
    "strasse": "Hauptstr. 5", "plz": "50667", "ort": "Köln", "land": "de",
    "email": "info@example.de", "elektronische_adresse": "rechnung@example.de",
    "kontakt_name": "Erika Muster", "kontakt_telefon": "0221 123", "kontakt_email": "erika@example.de",
    "ust_id": "de 123 456 789", "iban": "de89 3704 0044 0532 0130 00", "bic": "cobadeffxxx",
    "zahlungsziel_tage": "30",
}


def bild(fmt="PNG", groesse=(400, 200), modus="RGBA", farbe=(200, 30, 30, 255)) -> bytes:
    puffer = io.BytesIO()
    img = Image.new(modus, groesse, farbe if modus == "RGBA" else farbe[:3])
    img.save(puffer, format=fmt)
    return puffer.getvalue()


# --- Prüfungen -------------------------------------------------------------

@pytest.mark.parametrize("iban, gueltig", [
    ("DE89 3704 0044 0532 0130 00", True),
    ("DE89370400440532013001", False),     # Prüfziffer falsch
    ("DE8937040044053201300", False),      # zu kurz
    ("AT61 1904 3002 3457 3201", True),
    ("", False),
])
def test_iban(iban, gueltig):
    assert service.iban_gueltig(iban) is gueltig


def test_iban_formatiert():
    assert service.iban_formatiert("de89370400440532013000") == "DE89 3704 0044 0532 0130 00"


def test_validierung_meldet_fehler():
    daten = service.aus_formular({"firma": "", "land": "DE", "plz": "123", "ust_id": "DE12",
                                  "iban": "DE00123", "bic": "XYZ", "zahlungsziel_tage": "abc",
                                  "kontakt_email": "falsch"})
    fehler = service.pruefen(daten)
    assert {"firma", "plz", "ust_id", "iban", "bic", "zahlungsziel_tage", "kontakt_email"} <= set(fehler)


# --- Speichern & Protokoll -------------------------------------------------

def test_neue_installation_ist_leer(con):
    s = service.laden(con)
    assert s["firma"] == "" and s["geaendert_am"] is None
    assert len(service.fehlende_pflichtangaben(s)) >= 8


def test_speichern_normalisiert_und_protokolliert(con):
    assert service.speichern(con, VOLLSTAENDIG)
    s = service.laden(con)
    assert s["ust_id"] == "DE123456789"
    assert s["iban"] == "DE89370400440532013000"
    assert s["bic"] == "COBADEFFXXX"
    assert s["land"] == "DE"
    assert s["zahlungsziel_tage"] == 30
    assert service.fehlende_pflichtangaben(s) == []

    eintrag = service.verlauf(con)[0]
    assert eintrag["aktion"] == "angelegt"
    assert eintrag["details"]["felder"]["Firmenname"] == ["", "Ene Mene Media GmbH"]


def test_aenderung_wird_mit_alt_und_neu_protokolliert(con):
    service.speichern(con, VOLLSTAENDIG)
    assert service.speichern(con, {**VOLLSTAENDIG}) is False          # keine Änderung
    assert service.speichern(con, {**VOLLSTAENDIG, "ort": "Bonn", "plz": "53111", "kleinunternehmer": "1"})
    eintrag = service.verlauf(con)[0]
    assert eintrag["aktion"] == "geaendert"
    felder = eintrag["details"]["felder"]
    assert felder["Ort"] == ["Köln", "Bonn"]
    assert felder["Kleinunternehmer (§ 19 UStG)"] == ["nein", "ja"]
    assert len(service.verlauf(con)) == 2


def test_ungueltige_daten_werden_nicht_gespeichert(con):
    with pytest.raises(ValidierungsFehler):
        service.speichern(con, {**VOLLSTAENDIG, "iban": "DE00 1111"})
    assert service.laden(con)["firma"] == ""


def test_steuernummer_genuegt_statt_ust_id(con):
    service.speichern(con, {**VOLLSTAENDIG, "ust_id": "", "steuernummer": "214/5678/1234"})
    assert service.fehlende_pflichtangaben(service.laden(con)) == []


# --- Logo ------------------------------------------------------------------

def test_logo_wird_als_png_gespeichert_und_verkleinert(app, con):
    verz = app.config["DATENVERZEICHNIS"]
    breite, hoehe = service.logo_speichern(con, verz, bild("JPEG", (3000, 1500), "RGB"), "logo.jpg")
    assert (breite, hoehe) == (1200, 600)
    with Image.open(service.logo_pfad(verz)) as img:
        assert img.format == "PNG"
    assert service.laden(con)["logo_aktualisiert_am"]
    assert service.verlauf(con)[0]["aktion"] == "logo"


def test_transparenter_rand_wird_abgeschnitten(app, con):
    img = Image.new("RGBA", (300, 300), (0, 0, 0, 0))
    img.paste((10, 10, 10, 255), (100, 120, 200, 180))
    puffer = io.BytesIO()
    img.save(puffer, "PNG")
    groesse = service.logo_speichern(con, app.config["DATENVERZEICHNIS"], puffer.getvalue())
    assert groesse == (100, 60)


def test_altes_logo_wird_archiviert(app, con):
    verz = app.config["DATENVERZEICHNIS"]
    service.logo_speichern(con, verz, bild())
    service.logo_speichern(con, verz, bild(farbe=(0, 0, 255, 255)))
    archiv = list((service.logo_pfad(verz).parent / "logo_archiv").iterdir())
    assert len(archiv) == 1
    service.logo_entfernen(con, verz)
    assert not service.logo_vorhanden(verz)
    assert len(list(archiv[0].parent.iterdir())) == 2
    with pytest.raises(StammdatenFehler):
        service.logo_entfernen(con, verz)


@pytest.mark.parametrize("daten, meldung", [
    (b"", "auswählen"),
    (b"<svg xmlns='http://www.w3.org/2000/svg'></svg>", "nicht als Bild"),
    (b"kein bild", "nicht als Bild"),
])
def test_ungueltige_logos(app, con, daten, meldung):
    with pytest.raises(StammdatenFehler, match=meldung):
        service.logo_speichern(con, app.config["DATENVERZEICHNIS"], daten)


def test_falsches_bildformat(app, con):
    with pytest.raises(StammdatenFehler, match="PNG, JPG oder WebP"):
        service.logo_speichern(con, app.config["DATENVERZEICHNIS"], bild("BMP", modus="RGB"))


def test_zu_grosse_datei(app, con):
    with pytest.raises(StammdatenFehler, match="5 MB"):
        service.logo_speichern(con, app.config["DATENVERZEICHNIS"], b"0" * (service.LOGO_MAX_BYTES + 1))


# --- Weboberfläche ---------------------------------------------------------

def test_erster_start_fuehrt_zu_stammdaten(client):
    assert client.get("/").headers["Location"].endswith("/stammdaten/")
    client.post("/stammdaten/", data=VOLLSTAENDIG)
    assert client.get("/").headers["Location"].endswith("/kunden/")


def test_web_speichern_und_fehler(client):
    seite = client.get("/stammdaten/").get_data(as_text=True)
    assert "Bitte erfassen Sie zuerst" in seite

    r = client.post("/stammdaten/", data={**VOLLSTAENDIG, "iban": "DE00"})
    assert r.status_code == 422
    assert "IBAN ist ungültig" in r.get_data(as_text=True)

    r = client.post("/stammdaten/", data=VOLLSTAENDIG, follow_redirects=True)
    text = r.get_data(as_text=True)
    assert "Stammdaten gespeichert" in text
    assert "DE89 3704 0044 0532 0130 00" in text
    assert "Alle Pflichtangaben" in text


def test_web_logo(client):
    assert client.get("/stammdaten/logo.png").status_code == 404
    r = client.post("/stammdaten/logo", data={"logo": (io.BytesIO(bild()), "logo.png")},
                    content_type="multipart/form-data", follow_redirects=True)
    assert "Logo gespeichert" in r.get_data(as_text=True)
    logo = client.get("/stammdaten/logo.png")
    assert logo.status_code == 200 and logo.mimetype == "image/png"
    assert logo.headers["X-Content-Type-Options"] == "nosniff"

    r = client.post("/stammdaten/logo", data={"logo": (io.BytesIO(b"x"), "boese.png")},
                    content_type="multipart/form-data", follow_redirects=True)
    assert "nicht als Bild" in r.get_data(as_text=True)

    r = client.post("/stammdaten/logo/entfernen", follow_redirects=True)
    assert "Logo entfernt" in r.get_data(as_text=True)
    assert client.get("/stammdaten/logo.png").status_code == 404


def test_web_upload_zu_gross(client):
    r = client.post("/stammdaten/logo", data={"logo": (io.BytesIO(b"0" * (7 * 1024 * 1024)), "riesig.png")},
                    content_type="multipart/form-data", follow_redirects=True)
    assert "zu groß" in r.get_data(as_text=True)
