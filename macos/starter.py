"""macOS-Starter für „Buchhaltung für KMU“.

Wird vom Programm im App-Bundle gestartet (Contents/MacOS/Buchhaltung). Ablauf:
  1. Datenordner bestimmen – beim ersten Start fragen (Vorschlag: ~/Documents/Buchhaltung für KMU)
  2. Vollversion: ohne Lizenz im Datenordner Lizenznehmer und Lizenznummer abfragen;
     ohne gültige Lizenz startet die Vollversion nicht (Gegenstück zur Lizenzabfrage im
     Windows-Installer). Die Testversion fragt nicht.
  3. Läuft das Programm bereits, nur den Browser öffnen
  4. Sonst den lokalen Server (waitress, nur 127.0.0.1) starten und den Browser öffnen
Beendet wird über den Knopf „Beenden“ in der Oberfläche.

Aufbau des App-Bundles (Contents/Resources):
  python-arm64/, python-x86_64/   Python-Laufzeit je Prozessor (Apple Silicon / Intel)
  programm/                       Anwendung (app/, werkzeuge/)
  pruefung/                       Regelwerke der XRechnung-Prüfung (optional)
  starter.py                      diese Datei
  variante.txt                    „voll“ oder „test“

Einstellungen (Datenordner) liegen in ~/Library/Application Support/Buchhaltung für KMU/, weil das
App-Bundle schreibgeschützt bleiben muss (sonst ist seine Signatur ungültig).
"""

from __future__ import annotations

import configparser
import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
from logging.handlers import RotatingFileHandler
from pathlib import Path

PROGRAMM = "Buchhaltung für KMU"
BASIS = Path(__file__).resolve().parent
STANDARD_PORT = 8765
UNTERSTUETZUNG = Path.home() / "Library" / "Application Support" / "Buchhaltung für KMU"
EINSTELLUNGEN = UNTERSTUETZUNG / "einstellungen.ini"
ZUSTAND = UNTERSTUETZUNG / "laufend.json"

sys.path.insert(0, str(BASIS / "programm"))


def variante() -> str:
    try:
        return (BASIS / "variante.txt").read_text(encoding="utf-8").strip() or "voll"
    except OSError:
        return "voll"


# ---------------------------------------------------------------------------
# Dialoge (AppleScript – Texte werden als Argumente übergeben, nicht in das Skript eingesetzt)
# ---------------------------------------------------------------------------

class Abgebrochen(Exception):
    pass


def _osascript(zeilen: list[str], *argumente: str) -> str:
    skript = ["on run argv", *zeilen, "end run"]
    befehl = ["osascript"]
    for z in skript:
        befehl += ["-e", z]
    erg = subprocess.run([*befehl, *argumente], capture_output=True, text=True)
    if erg.returncode != 0:
        if "-128" in erg.stderr:          # „Abbrechen“ bzw. „Beenden“ gewählt
            raise Abgebrochen()
        raise RuntimeError(erg.stderr.strip() or "osascript fehlgeschlagen")
    return erg.stdout.rstrip("\n")


def meldung(text: str, fehler: bool = False) -> None:
    try:
        _osascript([f'display alert (item 1 of argv) message (item 2 of argv) as {"critical" if fehler else "informational"}'],
                   PROGRAMM, text)
    except Exception:
        print(text, file=sys.stderr)


def eingabe(text: str, vorgabe: str = "", knopf: str = "Weiter") -> str:
    """Textfeld; „Beenden“ löst Abgebrochen aus."""
    return _osascript([
        "set erg to display dialog (item 1 of argv) default answer (item 2 of argv) with title (item 3 of argv) "
        f'buttons {{"Beenden", "{knopf}"}} default button "{knopf}" cancel button "Beenden" with icon note',
        "return text returned of erg",
    ], text, vorgabe, PROGRAMM)


# ---------------------------------------------------------------------------
# Datenordner
# ---------------------------------------------------------------------------

def _einstellungen() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read(EINSTELLUNGEN, encoding="utf-8")
    return cfg


def datenordner_waehlen(vorschlag: Path) -> Path:
    while True:
        knopf = _osascript([
            "set erg to display dialog (item 1 of argv) with title (item 2 of argv) "
            'buttons {"Beenden", "Anderen Ordner wählen …", "OK"} default button "OK" cancel button "Beenden" with icon note',
            "return button returned of erg",
        ], "Willkommen bei Buchhaltung für KMU!\n\nIhre Daten – Datenbank sowie je Kunde Angebote, Rechnungen "
           "(XML und PDF) und Dokumente – werden in diesem Ordner gespeichert:\n\n"
           f"{vorschlag}\n\nWählen Sie einen Ordner, der regelmäßig gesichert wird.", PROGRAMM)
        if knopf == "OK":
            return vorschlag
        try:
            gewaehlt = _osascript(['return POSIX path of (choose folder with prompt (item 1 of argv))'],
                                  "Ordner für die Daten von Buchhaltung für KMU wählen")
        except Abgebrochen:
            continue
        pfad = Path(gewaehlt)
        if BASIS.parents[1] in pfad.parents or pfad == BASIS.parents[1]:
            meldung("Der Datenordner darf nicht im Programm liegen. Bitte einen anderen Ordner wählen.", True)
            continue
        try:
            belegt = any(pfad.iterdir())
        except OSError:
            belegt = True
        # Ein nicht leerer Ordner (z. B. „Dokumente“) bekommt einen eigenen Unterordner
        vorschlag = pfad / "Buchhaltung für KMU" if belegt and not (pfad / "xrechnung.db").exists() else pfad


def datenordner(fragen: bool = True) -> Path:
    if os.environ.get("XRECHNUNG_DATEN"):
        return Path(os.environ["XRECHNUNG_DATEN"])
    wert = _einstellungen().get("Buchhaltung", "datenordner", fallback="").strip()
    if wert:
        return Path(os.path.expanduser(wert))
    standard = Path.home() / "Documents" / "Buchhaltung für KMU"
    if not fragen:
        return standard
    gewaehlt = datenordner_waehlen(standard)
    UNTERSTUETZUNG.mkdir(parents=True, exist_ok=True)
    cfg = _einstellungen()
    cfg["Buchhaltung"] = {"datenordner": str(gewaehlt)}
    with EINSTELLUNGEN.open("w", encoding="utf-8") as f:
        cfg.write(f)
    return gewaehlt


# ---------------------------------------------------------------------------
# Lizenz (nur Vollversion)
# ---------------------------------------------------------------------------

def lizenz_sicherstellen(daten: Path) -> None:
    """Vollversion: Ohne Lizenzdatei im Datenordner wird die Lizenz abgefragt.

    Liegt bereits eine Lizenz vor (auch eine abgelaufene), startet das Programm – die Daten
    müssen immer zugänglich bleiben; ohne gültige Lizenz läuft es dann als Testversion."""
    from app import lizenz

    if (daten / lizenz.DATEI).is_file():
        return
    name, nummer = "", ""
    while True:
        name = eingabe("Bitte geben Sie Ihre Lizenz ein.\n\nLizenznehmer (genau wie in Ihrer Lizenz-E-Mail):", name)
        nummer = eingabe("Lizenznummer (beginnt mit XRK1-, am besten aus der E-Mail kopieren und einfügen):",
                         nummer, knopf="Lizenz aktivieren")
        try:
            l = lizenz.pruefen(name, nummer)
        except lizenz.LizenzFehler as e:
            meldung(f"Die Lizenznummer ist nicht gültig:\n{e}\n\nOhne Lizenznummer können Sie die Testversion "
                    "verwenden (eigene Download-Datei).", fehler=True)
            continue
        lizenz.speichern(daten, l)
        logging.getLogger("starter").info("Lizenz aktiviert: %s (Seriennummer %s)", l.lizenznehmer, l.seriennummer)
        return


# ---------------------------------------------------------------------------
# Laufende Instanz erkennen
# ---------------------------------------------------------------------------

def kennung(port: int) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/system/kennung", timeout=2) as antwort:
            daten = json.loads(antwort.read().decode("utf-8"))
            return daten if daten.get("app") == "buchhaltung-kmu" else None
    except Exception:
        return None


def port_frei(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def freier_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def laufende_instanz(daten: Path) -> int | None:
    kandidaten = [STANDARD_PORT]
    try:
        kandidaten.insert(0, int(json.loads(ZUSTAND.read_text(encoding="utf-8"))["port"]))
    except Exception:
        pass
    for port in dict.fromkeys(kandidaten):
        info = kennung(port)
        if info and Path(info.get("daten", "")).resolve() == daten.resolve():
            return port
    return None


def laufende_beenden() -> int:
    """Beendet eine laufende Instanz (z. B. vor einer Aktualisierung)."""
    try:
        zustand = json.loads(ZUSTAND.read_text(encoding="utf-8"))
        port, pid = int(zustand["port"]), int(zustand["pid"])
    except Exception:
        return 0
    if not kennung(port):
        return 0
    url = f"http://127.0.0.1:{port}"
    try:
        anfrage = urllib.request.Request(f"{url}/system/beenden", data=b"", method="POST", headers={"Origin": url})
        urllib.request.urlopen(anfrage, timeout=5).read()
    except Exception:
        pass
    for _ in range(50):
        if not kennung(port):
            return 0
        time.sleep(0.1)
    try:
        os.kill(pid, 15)
    except OSError:
        pass
    return 0


# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------

def protokoll_einrichten(daten: Path) -> logging.Logger:
    ordner = daten / "protokoll"
    ordner.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(ordner / "programm.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    return logging.getLogger("starter")


def main(argv: list[str]) -> int:
    if "--beenden" in argv:
        return laufende_beenden()
    ohne_browser = "--ohne-browser" in argv
    try:
        daten = datenordner()
    except Abgebrochen:
        return 0
    try:
        daten.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        meldung(f"Der Datenordner kann nicht angelegt werden:\n{daten}\n\n{e}", fehler=True)
        return 1
    log = protokoll_einrichten(daten)

    port = laufende_instanz(daten)
    if port:
        log.info("Programm läuft bereits auf Port %s – öffne Browser", port)
        if not ohne_browser:
            webbrowser.open(f"http://127.0.0.1:{port}/")
        return 0

    if variante() == "voll":
        try:
            lizenz_sicherstellen(daten)
        except Abgebrochen:
            log.info("Lizenzeingabe abgebrochen – Programm wird nicht gestartet")
            return 0

    pruefung = BASIS / "pruefung"
    if pruefung.is_dir() and "XRECHNUNG_ARTEFAKTE" not in os.environ:
        os.environ["XRECHNUNG_ARTEFAKTE"] = str(pruefung)

    port = STANDARD_PORT if port_frei(STANDARD_PORT) else freier_port()
    url = f"http://127.0.0.1:{port}/"

    from waitress.server import create_server
    from app import __version__, create_app

    def beenden() -> None:
        log.info("Beenden über die Oberfläche")

        def ende() -> None:
            try:
                ZUSTAND.unlink(missing_ok=True)
            except Exception:
                pass
            log.info("Beendet")
            logging.shutdown()
            os._exit(0)

        t = threading.Timer(0.5, ende)
        t.daemon = True
        t.start()

    app = create_app(daten, beenden=beenden)
    server = create_server(app, host="127.0.0.1", port=port, threads=4, ident=PROGRAMM)
    UNTERSTUETZUNG.mkdir(parents=True, exist_ok=True)
    ZUSTAND.write_text(json.dumps({"port": port, "pid": os.getpid(), "daten": str(daten)}), encoding="utf-8")
    log.info("%s %s (%s, %s) gestartet: %s (Daten: %s)", PROGRAMM, __version__, variante(),
             os.uname().machine, url, daten)

    if not ohne_browser:
        def browser_oeffnen() -> None:
            for _ in range(50):
                if kennung(port):
                    break
                time.sleep(0.1)
            webbrowser.open(url)
        threading.Thread(target=browser_oeffnen, daemon=True).start()

    try:
        server.run()
    except OSError:
        pass
    finally:
        try:
            if json.loads(ZUSTAND.read_text(encoding="utf-8")).get("pid") == os.getpid():
                ZUSTAND.unlink()
        except Exception:
            pass
        log.info("Beendet")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception:
        text = traceback.format_exc()
        try:
            logging.getLogger("starter").error(text)
        finally:
            meldung(f"{PROGRAMM} konnte nicht gestartet werden.\n\n{text[-1500:]}", fehler=True)
        sys.exit(1)
