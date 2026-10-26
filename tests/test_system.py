"""Systemfunktionen für den Windows-Betrieb: Kennung und Beenden über die Oberfläche."""

from app import APP_KENNUNG, __version__, create_app


def test_kennung(tmp_path):
    antwort = create_app(tmp_path).test_client().get("/system/kennung")
    assert antwort.status_code == 200
    assert antwort.json == {"app": APP_KENNUNG, "version": __version__, "daten": str(tmp_path)}


def test_beenden_ohne_desktopbetrieb_nicht_verfuegbar(tmp_path):
    client = create_app(tmp_path).test_client()
    assert client.post("/system/beenden", headers={"Origin": "http://localhost"}).status_code == 404
    assert b"Beenden" not in client.get("/kunden/").data


def test_beenden_nur_von_eigener_seite(tmp_path):
    aufrufe = []
    client = create_app(tmp_path, beenden=lambda: aufrufe.append(1)).test_client()
    assert client.post("/system/beenden").status_code == 403
    assert client.post("/system/beenden", headers={"Origin": "http://boese.example"}).status_code == 403
    assert aufrufe == []
    assert b"beenden-knopf" in client.get("/kunden/").data
    antwort = client.post("/system/beenden", headers={"Origin": "http://localhost"})
    assert antwort.status_code == 200 and aufrufe == [1]
