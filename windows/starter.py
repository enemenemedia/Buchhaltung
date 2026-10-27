"""Windows-Starter für „X-Rechnung für KMU“.

Wird über die Verknüpfung im Startmenü bzw. auf dem Desktop mit pythonw.exe gestartet
(kein Konsolenfenster). Ablauf:
  1. Datenordner bestimmen (Einstellung des Installers, sonst Dokumente\\X-Rechnung)
  2. Läuft das Programm bereits, nur den Browser öffnen
  3. Sonst den lokalen Server (waitress, nur 127.0.0.1) starten und den Browser öffnen
Beendet wird über den Knopf „Beenden“ in der Oberfläche.

Aufbau des Installationsordners:
  python\\         Python-Laufzeit mit allen Paketen
  programm\\       Anwendung (app\\, werkzeuge\\)
  pruefung\\       Regelwerke für die XRechnung-Prüfung (XSD, EN 16931, KoSIT)
  starter.py      diese Datei
  einstellungen.ini   vom Installer geschrieben (Datenordner)

Der Installer ruft den Starter außerdem mit --lizenz-pruefen <ini> auf, um die eingegebene
Lizenznummer zu prüfen, bevor er etwas installiert (Rückgabewert 0 = gültig).
"""

from __future__ import annotations

import configparser
import json
import logging
import os
import socket
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
from logging.handlers import RotatingFileHandler
from pathlib import Path

PROGRAMM = "X-Rechnung für KMU"
BASIS = Path(__file__).resolve().parent
STANDARD_PORT = 8765
ZUSTAND = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "X-Rechnung" / "laufend.json"

sys.path.insert(0, str(BASIS / "programm"))


# ---------------------------------------------------------------------------
# Windows-Hilfen
# ---------------------------------------------------------------------------

def meldung(text: str, fehler: bool = False) -> None:
    """Meldungsfenster (pythonw.exe hat keine Konsole)."""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, PROGRAMM, 0x10 if fehler else 0x40)
    except Exception:  # außerhalb von Windows
        print(text, file=sys.stderr)


def dokumente_ordner() -> Path:
    """Tatsächlicher Ordner „Dokumente“ (berücksichtigt Umleitungen, z. B. nach OneDrive)."""
    try:
        import ctypes
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                        ("Data4", wintypes.BYTE * 8)]

        # FOLDERID_Documents {FDD39AD0-238F-46AF-ADB4-6C85480369C7}
        fid = GUID(0xFDD39AD0, 0x238F, 0x46AF, (wintypes.BYTE * 8)(0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7))
        pfad = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(fid), 0, None, ctypes.byref(pfad)) == 0:
            ergebnis = Path(pfad.value)
            ctypes.windll.ole32.CoTaskMemFree(pfad)
            return ergebnis
    except Exception:
        pass
    return Path.home() / "Documents"


def datenordner() -> Path:
    if os.environ.get("XRECHNUNG_DATEN"):
        return Path(os.environ["XRECHNUNG_DATEN"])
    ini = BASIS / "einstellungen.ini"
    if ini.is_file():
        cfg = configparser.ConfigParser()
        cfg.read(ini, encoding="utf-8")
        wert = cfg.get("X-Rechnung", "datenordner", fallback="").strip()
        if wert:
            return Path(os.path.expandvars(wert))
    return dokumente_ordner() / "X-Rechnung"


# ---------------------------------------------------------------------------
# Laufende Instanz erkennen
# ---------------------------------------------------------------------------

def kennung(port: int) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/system/kennung", timeout=2) as antwort:
            daten = json.loads(antwort.read().decode("utf-8"))
            return daten if daten.get("app") == "xrechnung-kmu" else None
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
    """Port einer bereits laufenden Instanz mit demselben Datenordner."""
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


def prozess_beenden(pid: int, wartezeit_ms: int) -> None:
    """Wartet, bis der Prozess beendet ist; erzwingt das Ende nach Ablauf der Wartezeit."""
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        SYNCHRONIZE, PROCESS_TERMINATE = 0x00100000, 0x0001
        h = k32.OpenProcess(SYNCHRONIZE | PROCESS_TERMINATE, False, pid)
        if not h:
            return                                  # läuft nicht (mehr)
        try:
            if k32.WaitForSingleObject(h, wartezeit_ms) != 0:   # 0 = WAIT_OBJECT_0
                k32.TerminateProcess(h, 1)
                k32.WaitForSingleObject(h, 5000)
        finally:
            k32.CloseHandle(h)
    except Exception:
        pass


def laufende_beenden() -> int:
    """Beendet eine laufende Instanz (für Installer und Deinstallation).

    Kehrt erst zurück, wenn der Prozess wirklich beendet ist – sonst wären
    Dateien der Python-Laufzeit beim Überschreiben noch gesperrt.
    """
    ports = [STANDARD_PORT]
    pid = None
    try:
        zustand = json.loads(ZUSTAND.read_text(encoding="utf-8"))
        ports.insert(0, int(zustand["port"]))
        pid = int(zustand["pid"])
    except Exception:
        pass
    gefunden = False
    for port in dict.fromkeys(ports):
        if kennung(port):
            gefunden = True
            url = f"http://127.0.0.1:{port}"
            anfrage = urllib.request.Request(f"{url}/system/beenden", data=b"", method="POST",
                                             headers={"Origin": url})
            try:
                urllib.request.urlopen(anfrage, timeout=5).read()
            except Exception:
                pass
            for _ in range(50):
                if not kennung(port):
                    break
                time.sleep(0.1)
    # Nur wenn die Instanz geantwortet hat – eine alte PID könnte inzwischen
    # zu einem fremden Prozess gehören.
    if gefunden and pid and pid != os.getpid():
        prozess_beenden(pid, 10_000)
    return 0


def lizenz_pruefen(ini: str) -> int:
    """Für den Installer: prüft Lizenznehmer/Lizenznummer aus der INI-Datei.

    Gültig: schreibt die Lizenz in einheitlicher Form zurück, Rückgabe 0.
    Ungültig: schreibt den Grund als „fehler“ in die Datei, Rückgabe 2."""
    from app import lizenz

    pfad = Path(ini)
    name, nummer = lizenz.datei_lesen(pfad)
    try:
        l = lizenz.pruefen(name, nummer)
    except lizenz.LizenzFehler as e:
        cfg = configparser.ConfigParser(interpolation=None)
        cfg["Lizenz"] = {"lizenznehmer": name, "lizenznummer": nummer, "fehler": str(e)}
        with pfad.open("w", encoding="utf-8") as f:
            cfg.write(f)
        return 2
    lizenz.speichern(pfad.parent, l)
    if pfad.name != lizenz.DATEI:
        (pfad.parent / lizenz.DATEI).replace(pfad)
    return 0


def main(argv: list[str]) -> int:
    if "--beenden" in argv:
        return laufende_beenden()
    if "--lizenz-pruefen" in argv:
        return lizenz_pruefen(argv[argv.index("--lizenz-pruefen") + 1])
    ohne_browser = "--ohne-browser" in argv
    daten = datenordner()
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

    pruefung = BASIS / "pruefung"
    if pruefung.is_dir() and "XRECHNUNG_ARTEFAKTE" not in os.environ:
        os.environ["XRECHNUNG_ARTEFAKTE"] = str(pruefung)

    port = STANDARD_PORT if port_frei(STANDARD_PORT) else freier_port()
    url = f"http://127.0.0.1:{port}/"

    from waitress.server import create_server
    from app import __version__, create_app

    server = None

    def beenden() -> None:
        log.info("Beenden über die Oberfläche")

        def ende() -> None:
            # Alle Schreibvorgänge sind abgeschlossen (je Anfrage eine Transaktion);
            # der Prozess endet sofort, damit Installer/Deinstallation die Dateien freibekommen.
            try:
                ZUSTAND.unlink(missing_ok=True)
            except Exception:
                pass
            log.info("Beendet")
            logging.shutdown()
            os._exit(0)

        t = threading.Timer(0.5, ende)      # Antwort an den Browser noch ausliefern
        t.daemon = True
        t.start()

    app = create_app(daten, beenden=beenden)
    server = create_server(app, host="127.0.0.1", port=port, threads=4, ident=PROGRAMM)
    ZUSTAND.parent.mkdir(parents=True, exist_ok=True)
    ZUSTAND.write_text(json.dumps({"port": port, "pid": os.getpid(), "daten": str(daten)}), encoding="utf-8")
    log.info("%s %s gestartet: %s (Daten: %s)", PROGRAMM, __version__, url, daten)

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
        pass  # beim Schließen über „Beenden“
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
