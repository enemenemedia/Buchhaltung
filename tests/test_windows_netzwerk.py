"""Windows-Netzwerkversion: Server mit Daten auf dem Netzlaufwerk, Sperre, Arbeitsplatz, Beenden."""

import importlib.util
import json
import socket
import ssl
import threading
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.netzwerk import benutzer as bn
from app import db

PFAD = Path(__file__).resolve().parents[1] / "windows" / "netzwerk_starter.py"


def frei():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def ns(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("netzwerk_starter", PFAD)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    monkeypatch.setattr(m, "LOKAL", tmp_path / "lokal")
    monkeypatch.setattr(m, "ZUSTAND", tmp_path / "lokal" / "server.json")
    monkeypatch.setattr(m, "LEBENSZEICHEN_S", 0.3)
    monkeypatch.delenv("BUCHHALTUNG_DATEN", raising=False)
    return m


def ini(tmp_path, **werte):
    text = "[Netzwerk]\n" + "".join(f"{k} = {v}\n" for k, v in werte.items())
    p = tmp_path / "einstellungen.ini"
    p.write_text(text, encoding="utf-16")
    return p


def anfrage(url, ca, daten=None, kopf=None):
    ctx = ssl.create_default_context(cafile=str(ca))
    r = urllib.request.Request(url, data=daten, headers=kopf or {}, method="POST" if daten is not None else "GET")
    try:
        with urllib.request.urlopen(r, context=ctx, timeout=20) as a:
            return a.status, a.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def test_einstellungen_und_datenordner(ns, tmp_path):
    p = ini(tmp_path, modus="arbeitsplatz", freigabe=r"\\nas\buero", laufwerk="N:", port="9443")
    cfg = ns.einstellungen(p)
    assert cfg["modus"] == "arbeitsplatz" and ns.port(cfg) == 9443
    assert ns.basisordner(cfg) == Path(r"\\nas\buero") / "Buchhaltung für KMU"
    assert ns.datenordner(cfg) == Path(r"\\nas\buero") / "Buchhaltung für KMU" / "Daten"
    assert ns.datenordner({"datenordner": "X", "freigabe": "Y"}) == Path("X")
    assert ns.info_aktuell({"lebenszeichen": ns.jetzt()})
    alt = (datetime.now() - timedelta(minutes=5)).replace(microsecond=0).isoformat(sep=" ")
    assert not ns.info_aktuell({"lebenszeichen": alt}) and not ns.info_aktuell(None)


def test_server_auf_netzlaufwerk(ns, tmp_path):
    pytest.importorskip("cheroot")
    basis = tmp_path / "freigabe" / "Buchhaltung für KMU"
    daten = basis / "Daten"
    daten.mkdir(parents=True)
    cfg = {"port": str(frei()), "ordner": str(basis), "hostnamen": "buero-server"}
    s = ns.NetzwerkServer(cfg, ns.datenordner(cfg), ns.basisordner(cfg))
    app = s.vorbereiten()
    assert app is not None
    t = threading.Thread(target=s.laufen, args=(app,), daemon=True)
    t.start()
    for _ in range(300):                                       # bis 30 s (langsame CI-Rechner)
        if ns.kennung(s.port):
            break
        time.sleep(0.1)
    assert ns.kennung(s.port), "Server ist nicht gestartet"
    ca = basis / ns.CA_DATEI                                  # öffentliches CA-Zertifikat auf der Freigabe
    assert ca.read_bytes() == (ns.LOKAL / "zertifikat" / "ca.crt").read_bytes()
    assert (daten / "xrechnung.db").exists()                  # Daten im Unterordner
    # private Schlüssel (Zertifikat, Sitzungen) bleiben lokal auf dem Server-PC
    assert not list(basis.rglob("*.key")) and not list(basis.rglob(".secret_key"))
    assert (ns.LOKAL / ".secret_key").exists()

    info = json.loads((basis / ns.INFO_DATEI).read_text(encoding="utf-8"))
    assert info["port"] == s.port and "buero-server" in info["adressen"] and ns.info_aktuell(info)
    erstes = info["lebenszeichen"]
    time.sleep(1.2)
    assert json.loads((basis / ns.INFO_DATEI).read_text(encoding="utf-8"))["lebenszeichen"] >= erstes

    # HTTPS mit dem verteilten CA-Zertifikat; Einrichtung am Server-PC ohne Code
    status, html = anfrage(f"https://localhost:{s.port}/einrichtung", ca)
    assert status == 200 and "Einrichtungscode" not in html.split("<form")[1]
    assert (ns.LOKAL / "einrichtungscode.txt").exists() and not (daten / "einrichtungscode.txt").exists()
    con = db.verbinden(daten / "xrechnung.db")
    assert con.execute("SELECT COUNT(*) FROM benutzer").fetchone()[0] == 0
    con.close()

    # zweiter Server mit denselben Daten (z. B. auf einem anderen PC) startet nicht
    zweiter = ns.NetzwerkServer({"port": str(frei())}, daten, basis)
    assert zweiter.vorbereiten() is None

    # Arbeitsplatz findet den Server über die Datei auf dem Netzlaufwerk
    geoeffnet = []
    assert ns.arbeitsplatz({"ordner": str(basis)}, oeffnen=geoeffnet.append) == 0
    assert geoeffnet and geoeffnet[0].endswith(f":{s.port}/")

    # Beenden nur mit Steuer-Kennwort
    assert anfrage(f"https://localhost:{s.port}/system/stopp", ca, b"", {"X-Steuer-Token": "falsch"})[0] == 403
    assert ns.beenden() == 0
    t.join(10)
    assert not t.is_alive() and not ns.kennung(s.port)
    assert not (basis / ns.INFO_DATEI).exists() and not ns.ZUSTAND.exists()
    # danach darf ein Server (wieder) starten
    nochmal = ns.NetzwerkServer({"port": str(frei())}, daten, basis)
    assert nochmal.vorbereiten() is not None
    nochmal.aufraeumen()


def test_arbeitsplatz_ohne_server(ns, tmp_path, monkeypatch):
    meldungen = []
    monkeypatch.setattr(ns, "meldung", lambda t, fehler=False: meldungen.append(t))
    daten = tmp_path / "leer"
    daten.mkdir()
    assert ns.arbeitsplatz({"ordner": str(daten)}, oeffnen=lambda u: None) == 1
    assert "noch kein Server" in meldungen[-1]
    (daten / ns.INFO_DATEI).write_text(json.dumps({"pc": "BUERO", "port": frei(), "adressen": ["127.0.0.1"],
                                                   "lebenszeichen": ns.jetzt()}), encoding="utf-8")
    assert ns.arbeitsplatz({"ordner": str(daten)}, oeffnen=lambda u: None) == 1
    assert "BUERO" in meldungen[-1] and "antwortet nicht" in meldungen[-1]


def test_stopp_ohne_token_nicht_vorhanden(tmp_path):
    from app import create_app
    a = create_app(tmp_path, netzwerk=True)
    c = a.test_client()
    assert c.post("/system/stopp").status_code == 404
