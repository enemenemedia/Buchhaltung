"""Windows-Netzwerkversion von „Buchhaltung für KMU“ (LAN, Daten auf einem Netzlaufwerk).

Ein PC im Büro ist der **Server**: Er startet das Programm (HTTPS, Anmeldung mit Benutzern und
Rollen) und speichert alle Daten im Ordner auf dem Netzlaufwerk. Alle anderen PCs sind
**Arbeitsplätze**: Sie öffnen die Buchhaltung im Browser. Den Server finden sie über die Datei
„netzwerk-server.json“, die der Server auf dem Netzlaufwerk ablegt und laufend aktualisiert.

Aufrufe (pythonw.exe netzwerk_starter.py …):
  (ohne)            je nach Installation: Server starten bzw. Arbeitsplatz öffnen
  --server          Server starten (läuft schon einer auf diesem PC: nur Browser öffnen)
  --ohne-browser    Server ohne Browserfenster starten (Autostart)
  --arbeitsplatz    Server suchen, Zertifikat einmalig vertrauen, Browser öffnen
  --beenden         Server auf diesem PC beenden (Installer, Startmenü „Server beenden“)
  --status          Zustand als Meldung anzeigen

Ordner auf dem Netzlaufwerk:
  <Freigabe>\\Buchhaltung für KMU\\            netzwerk-server.json, Zertifikat (lesen alle PCs)
  <Freigabe>\\Buchhaltung für KMU\\Daten\\     Datenbank, Belege, Rechnungen (braucht nur der Server)
Sitzungsschlüssel, Zertifikatsschlüssel und Einrichtungscode bleiben lokal auf dem Server-PC.

Sicherheit gegen doppelte Server: Die Datei „netzwerk-server.sperre“ auf dem Netzlaufwerk wird
während der Laufzeit exklusiv gesperrt. Ein zweiter Server – auch auf einem anderen PC – startet
dann nicht, die SQLite-Datenbank wird nie von zwei Programmen gleichzeitig beschrieben.

Einstellungen (einstellungen.ini neben dieser Datei, vom Installer als UTF-16 geschrieben):
  [Netzwerk]
  modus       = server | arbeitsplatz
  freigabe    = \\\\SERVER\\Freigabe            (Netzwerkfreigabe)
  laufwerk    = N:                            (optional: Laufwerksbuchstabe für die Freigabe)
  ordner      = \\\\SERVER\\Freigabe\\Buchhaltung für KMU
  datenordner = \\\\SERVER\\Freigabe\\Buchhaltung für KMU\\Daten
  port        = 8443
  hostnamen   = buero-pc.firma.local           (optional, weitere Namen für das Zertifikat)
"""

from __future__ import annotations

import configparser
import hashlib
import json
import logging
import os
import secrets
import socket
import ssl
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

PROGRAMM = "Buchhaltung für KMU"
APP_KENNUNG = "buchhaltung-kmu"
INI_ABSCHNITT = "Netzwerk"
STANDARD_PORT = 8443
BASIS = Path(__file__).resolve().parent
LOKAL = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Buchhaltung-KMU-Netzwerk"
ZUSTAND = LOKAL / "server.json"                    # Port, PID, Steuer-Kennwort (nur dieses Benutzerprofil)
INFO_DATEI = "netzwerk-server.json"                # auf dem Netzlaufwerk: wo der Server erreichbar ist
SPERR_DATEI = "netzwerk-server.sperre"
CA_DATEI = "zertifikat-buchhaltung-kmu.crt"        # öffentliches CA-Zertifikat für die Arbeitsplätze
LEBENSZEICHEN_S = 30
VERALTET_S = 120
KEIN_FENSTER = 0x08000000                          # CREATE_NO_WINDOW

if (BASIS / "programm").is_dir():
    sys.path.insert(0, str(BASIS / "programm"))

log = logging.getLogger("netzwerk")


# ---------------------------------------------------------------------------
# Hilfen
# ---------------------------------------------------------------------------

def meldung(text: str, fehler: bool = False) -> None:
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, PROGRAMM, 0x10 if fehler else 0x40)
    except Exception:  # außerhalb von Windows
        print(text, file=sys.stderr)


def jetzt() -> str:
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def einstellungen(ini: Path | None = None) -> dict:
    ini = ini or BASIS / "einstellungen.ini"
    werte = {"modus": "server", "freigabe": "", "laufwerk": "", "datenordner": "", "port": str(STANDARD_PORT),
             "hostnamen": ""}
    if ini.is_file():
        from app.lizenz import ini_text           # UTF-16 (Installer), UTF-8 oder ANSI
        cfg = configparser.ConfigParser(interpolation=None)
        try:
            cfg.read_string(ini_text(ini))
        except configparser.Error:
            pass
        if cfg.has_section(INI_ABSCHNITT):
            werte.update({k: v.strip() for k, v in cfg.items(INI_ABSCHNITT)})
    return werte


def basisordner(cfg: dict) -> Path:
    """Ordner des Programms auf dem Netzlaufwerk (Server-Info und Zertifikat für alle PCs)."""
    if cfg.get("ordner"):
        return Path(cfg["ordner"])
    if cfg.get("freigabe"):
        return Path(cfg["freigabe"]) / PROGRAMM
    if cfg.get("datenordner"):
        return Path(cfg["datenordner"]).parent
    raise SystemExit("Kein Netzlaufwerk eingestellt – bitte die Installation wiederholen.")


def datenordner(cfg: dict) -> Path:
    if os.environ.get("BUCHHALTUNG_DATEN"):
        return Path(os.environ["BUCHHALTUNG_DATEN"])
    if cfg.get("datenordner"):
        return Path(cfg["datenordner"])
    return basisordner(cfg) / "Daten"


def port(cfg: dict) -> int:
    try:
        return int(cfg.get("port") or STANDARD_PORT)
    except ValueError:
        return STANDARD_PORT


def laufwerk_verbinden(cfg: dict) -> None:
    """Stellt die Laufwerkszuordnung (z. B. N: → \\\\SERVER\\Freigabe) wieder her, falls sie fehlt.
    Das Programm selbst arbeitet mit dem UNC-Pfad; der Buchstabe ist für den Explorer gedacht."""
    lw, freigabe = cfg.get("laufwerk", "").rstrip("\\"), cfg.get("freigabe", "")
    if os.name != "nt" or not lw or not freigabe or Path(lw + "\\").exists():
        return
    try:
        subprocess.run(["net", "use", lw, freigabe, "/persistent:yes"], capture_output=True, timeout=30,
                       creationflags=KEIN_FENSTER)
    except Exception as e:  # pragma: no cover
        log.warning("Laufwerk %s nicht verbunden: %s", lw, e)


def protokoll_einrichten() -> None:
    ordner = LOKAL / "protokoll"
    ordner.mkdir(parents=True, exist_ok=True)
    h = RotatingFileHandler(ordner / "netzwerk.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(h)


def eigene_adressen() -> list[str]:
    """Rechnername (auch mit Domäne) und IPv4-Adressen dieses PCs im lokalen Netz."""
    namen: list[str] = []
    name = socket.gethostname()
    namen.append(name)
    try:
        fqdn = socket.getfqdn()
        if fqdn and "." in fqdn and fqdn.lower() != name.lower():
            namen.append(fqdn)
    except OSError:
        pass
    ips: list[str] = []
    try:
        for info in socket.getaddrinfo(name, None, socket.AF_INET):
            ips.append(info[4][0])
    except OSError:
        pass
    try:  # Adresse der Standardroute (es wird nichts gesendet)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.168.255.255", 9))
            ips.append(s.getsockname()[0])
    except OSError:
        pass
    ips = [i for i in dict.fromkeys(ips) if not i.startswith(("127.", "169.254.", "0."))]
    return namen + ips


def atomar_schreiben(pfad: Path, text: str) -> None:
    tmp = pfad.with_name(pfad.name + f".{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, pfad)


def json_lesen(pfad: Path) -> dict | None:
    try:
        return json.loads(pfad.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def info_aktuell(info: dict | None, toleranz_s: int = VERALTET_S) -> bool:
    try:
        stand = datetime.fromisoformat(info["lebenszeichen"])
    except (TypeError, KeyError, ValueError):
        return False
    return abs((datetime.now() - stand).total_seconds()) <= toleranz_s


# ---------------------------------------------------------------------------
# Sperre auf dem Netzlaufwerk
# ---------------------------------------------------------------------------

class Sperre:
    """Exklusive Sperre einer Datei auf dem Netzlaufwerk für die Laufzeit des Servers.
    Windows: Byte-Range-Lock (msvcrt), wirkt über SMB auch für andere PCs. Stürzt der Server-PC ab,
    gibt der Dateiserver die Sperre frei, sobald die Verbindung abbricht."""

    def __init__(self, ordner: Path):
        self.pfad = ordner / SPERR_DATEI
        self._f = None

    def erwerben(self) -> bool:
        f = open(self.pfad, "a+b")
        try:
            if os.name == "nt":
                import msvcrt
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.close()
            return False
        self._f = f
        return True

    def freigeben(self) -> None:
        if self._f is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                self._f.seek(0)
                msvcrt.locking(self._f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._f.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        self._f.close()
        self._f = None


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

def _lokaler_kontext() -> ssl.SSLContext:
    # Nur für Anfragen an 127.0.0.1 auf diesem PC
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def kennung(p: int) -> dict | None:
    try:
        with urllib.request.urlopen(f"https://127.0.0.1:{p}/system/kennung", timeout=3,
                                    context=_lokaler_kontext()) as a:
            d = json.loads(a.read().decode("utf-8"))
            return d if d.get("app") == APP_KENNUNG else None
    except Exception:
        return None


def ca_bereitstellen(ca: Path, daten: Path) -> None:
    """Öffentliches CA-Zertifikat auf das Netzlaufwerk legen – die Arbeitsplätze importieren es."""
    ziel = daten / CA_DATEI
    inhalt = ca.read_bytes()
    try:
        if not ziel.exists() or ziel.read_bytes() != inhalt:
            atomar_schreiben(ziel, inhalt.decode("ascii"))
    except OSError as e:
        log.warning("CA-Zertifikat nicht auf dem Netzlaufwerk abgelegt: %s", e)


class NetzwerkServer:
    def __init__(self, cfg: dict, daten: Path, basis: Path | None = None):
        self.cfg, self.daten, self.port = cfg, daten, port(cfg)
        self.basis = basis or daten.parent
        self.sperre = Sperre(daten)
        self.token = secrets.token_urlsafe(24)
        self.adressen = eigene_adressen() + [h for h in cfg.get("hostnamen", "").split(",") if h.strip()]
        self._halt = threading.Event()
        self.server = None

    @property
    def url(self) -> str:
        return f"https://{self.adressen[0]}:{self.port}/"

    def info(self) -> dict:
        from app import __version__
        return {"app": APP_KENNUNG, "version": __version__, "pc": socket.gethostname(),
                "benutzer": os.environ.get("USERNAME", ""), "port": self.port, "url": self.url,
                "adressen": self.adressen, "gestartet": self.gestartet, "lebenszeichen": jetzt()}

    def _lebenszeichen(self) -> None:
        while not self._halt.wait(LEBENSZEICHEN_S):
            try:
                atomar_schreiben(self.basis / INFO_DATEI, json.dumps(self.info(), ensure_ascii=False, indent=1))
            except OSError as e:
                log.warning("Lebenszeichen nicht geschrieben (Netzlaufwerk?): %s", e)

    def stoppen(self) -> None:
        log.info("Server wird beendet")
        self._halt.set()
        if self.server is not None:
            threading.Timer(0.5, self.server.stop).start()

    def vorbereiten(self):
        """Sperre, Zertifikate und Anwendung – ohne den Server zu starten (auch für Tests)."""
        from app import create_app
        from app.netzwerk import zertifikat

        if not self.sperre.erwerben():
            return None
        z = zertifikat.sicherstellen(LOKAL / "zertifikat", self.adressen)
        ca_bereitstellen(z.ca, self.basis)
        self.zert = z
        self.gestartet = jetzt()
        app = create_app(self.daten, netzwerk=True, einstellungen={
            "ZERTIFIKAT_CA": str(z.ca),
            "EINRICHTUNGSCODE_ORDNER": str(LOKAL),
            "SCHLUESSEL_ORDNER": str(LOKAL),
            "EINRICHTUNG_LOKAL_OHNE_CODE": True,
            "STEUER_TOKEN": self.token,
            "STEUER_STOPP": self.stoppen,
        })
        atomar_schreiben(self.basis / INFO_DATEI, json.dumps(self.info(), ensure_ascii=False, indent=1))
        ZUSTAND.parent.mkdir(parents=True, exist_ok=True)
        atomar_schreiben(ZUSTAND, json.dumps({"port": self.port, "pid": os.getpid(), "token": self.token,
                                               "daten": str(self.daten)}))
        return app

    def aufraeumen(self) -> None:
        self._halt.set()
        try:
            info = json_lesen(self.basis / INFO_DATEI)
            if info and info.get("pc") == socket.gethostname() and info.get("gestartet") == getattr(self, "gestartet", None):
                (self.basis / INFO_DATEI).unlink()
        except OSError:
            pass
        self.sperre.freigeben()
        try:
            if (json_lesen(ZUSTAND) or {}).get("pid") == os.getpid():
                ZUSTAND.unlink()
        except OSError:
            pass

    def laufen(self, app, bereit=None) -> None:
        from cheroot import wsgi
        from cheroot.ssl.builtin import BuiltinSSLAdapter

        self.server = wsgi.Server(("0.0.0.0", self.port), app, numthreads=8, server_name=PROGRAMM,
                                  timeout=60, shutdown_timeout=5)
        adapter = BuiltinSSLAdapter(str(self.zert.server), str(self.zert.schluessel))
        adapter.context.minimum_version = ssl.TLSVersion.TLSv1_2
        self.server.ssl_adapter = adapter
        threading.Thread(target=self._lebenszeichen, daemon=True).start()
        if bereit:
            threading.Thread(target=bereit, daemon=True).start()
        try:
            self.server.safe_start()
        finally:
            self.aufraeumen()


def server_starten(cfg: dict, ohne_browser: bool = False) -> int:
    protokoll_einrichten()
    laufwerk_verbinden(cfg)
    daten, basis = datenordner(cfg), basisordner(cfg)
    p = port(cfg)

    # Läuft der Server schon auf diesem PC? Dann nur den Browser öffnen.
    zustand = json_lesen(ZUSTAND) or {}
    if zustand.get("port") and kennung(int(zustand["port"])):
        if not ohne_browser:
            webbrowser.open(f"https://localhost:{zustand['port']}/")
        return 0

    try:
        basis.mkdir(parents=True, exist_ok=True)
        daten.mkdir(parents=True, exist_ok=True)
        probe = daten / f".schreibtest-{os.getpid()}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as e:
        meldung(f"Das Netzlaufwerk ist nicht erreichbar oder nicht beschreibbar:\n{daten}\n\n{e}\n\n"
                "Bitte prüfen Sie, ob der Dateiserver/NAS eingeschaltet ist und Sie Schreibrechte haben.", fehler=True)
        return 1

    s = NetzwerkServer(cfg, daten, basis)
    app = s.vorbereiten()
    if app is None:
        info = json_lesen(basis / INFO_DATEI)
        wo = f" auf dem PC „{info.get('pc')}“ ({info.get('url')})" if info else ""
        meldung(f"Der Server von {PROGRAMM} läuft bereits{wo}.\n\nEs darf nur ein Server mit diesen Daten "
                "arbeiten. Dieser PC kann die Buchhaltung als Arbeitsplatz im Browser öffnen.", fehler=not info)
        if info and info_aktuell(info) and not ohne_browser:
            webbrowser.open(info["url"])
        return 2
    log.info("Server startet: %s (Daten: %s)", s.url, daten)

    def browser():
        for _ in range(100):
            if kennung(s.port):
                break
            time.sleep(0.1)
        if not ohne_browser:
            webbrowser.open(f"https://localhost:{s.port}/")

    try:
        s.laufen(app, bereit=browser)
    except OSError as e:
        s.aufraeumen()
        meldung(f"Der Server konnte Port {s.port} nicht öffnen:\n{e}\n\nLäuft ein anderes Programm auf diesem Port?",
                fehler=True)
        return 1
    log.info("Beendet")
    return 0


def prozess_warten(pid: int, wartezeit_ms: int) -> None:
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x00100000 | 0x0001, False, pid)          # SYNCHRONIZE | TERMINATE
        if not h:
            return
        try:
            if k32.WaitForSingleObject(h, wartezeit_ms) != 0:
                k32.TerminateProcess(h, 1)
                k32.WaitForSingleObject(h, 5000)
        finally:
            k32.CloseHandle(h)
    except Exception:
        pass


def beenden() -> int:
    """Beendet den Server dieses PCs (kehrt erst zurück, wenn der Prozess weg ist)."""
    z = json_lesen(ZUSTAND)
    if not z:
        return 0
    p, pid = int(z.get("port", STANDARD_PORT)), int(z.get("pid", 0))
    if not kennung(p):
        return 0
    anfrage = urllib.request.Request(f"https://127.0.0.1:{p}/system/stopp", data=b"", method="POST",
                                     headers={"X-Steuer-Token": z.get("token", "")})
    try:
        urllib.request.urlopen(anfrage, timeout=10, context=_lokaler_kontext()).read()
    except Exception:
        pass
    for _ in range(100):
        if not kennung(p):
            break
        time.sleep(0.1)
    if pid and pid != os.getpid():
        prozess_warten(pid, 10_000)
    return 0


# ---------------------------------------------------------------------------
# Arbeitsplatz
# ---------------------------------------------------------------------------

def erreichbar(host: str, p: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, p), timeout=timeout):
            return True
    except OSError:
        return False


def server_adresse(info: dict) -> str | None:
    """Erste erreichbare Adresse des Servers (Name bevorzugt, sonst IP)."""
    p = int(info.get("port", STANDARD_PORT))
    for host in info.get("adressen") or []:
        if erreichbar(host, p):
            return f"https://{host}:{p}/"
    return None


def ca_fingerabdruck(pfad: Path) -> str:
    der = ssl.PEM_cert_to_DER_cert(pfad.read_text(encoding="ascii"))
    return hashlib.sha1(der).hexdigest()


def ca_vertrauen(pfad: Path) -> bool:
    """Importiert das CA-Zertifikat einmalig in den Speicher „Vertrauenswürdige Stammzertifizierungsstellen“
    des angemeldeten Benutzers (keine Administratorrechte nötig; Windows fragt einmal nach)."""
    if os.name != "nt" or not pfad.is_file():
        return False
    merker = LOKAL / "vertraut.json"
    fp = ca_fingerabdruck(pfad)
    if (json_lesen(merker) or {}).get("ca") == fp:
        return True
    pruefen = subprocess.run(["certutil", "-user", "-verifystore", "Root", fp], capture_output=True,
                             creationflags=KEIN_FENSTER)
    if pruefen.returncode != 0:
        r = subprocess.run(["certutil", "-user", "-addstore", "Root", str(pfad)], capture_output=True,
                           creationflags=KEIN_FENSTER)
        if r.returncode != 0:
            log.warning("Zertifikat nicht importiert: %s", r.stdout[-300:])
            return False
    merker.parent.mkdir(parents=True, exist_ok=True)
    atomar_schreiben(merker, json.dumps({"ca": fp}))
    return True


def arbeitsplatz(cfg: dict, oeffnen=webbrowser.open) -> int:
    protokoll_einrichten()
    laufwerk_verbinden(cfg)
    try:
        daten = basisordner(cfg)
        info = json_lesen(daten / INFO_DATEI)
    except (OSError, SystemExit) as e:
        meldung(f"Das Netzlaufwerk ist nicht erreichbar:\n{e}", fehler=True)
        return 1
    if info is None:
        meldung(f"Auf dem Netzlaufwerk wurde noch kein Server von {PROGRAMM} gefunden:\n{daten}\n\n"
                "Bitte zuerst auf dem Server-PC die Netzwerkversion als „Server“ installieren und starten.",
                fehler=True)
        return 1
    ca_vertrauen(daten / CA_DATEI)
    url = server_adresse(info)
    if url is None:
        zuletzt = info.get("lebenszeichen", "?")
        meldung(f"Der Server-PC „{info.get('pc', '?')}“ antwortet nicht (letztes Lebenszeichen: {zuletzt}).\n\n"
                f"Bitte den Server-PC einschalten und dort {PROGRAMM} starten. Ist er an, prüfen Sie die "
                "Windows-Firewall auf dem Server-PC (siehe Anleitung).", fehler=True)
        return 1
    oeffnen(url)
    return 0


def status(cfg: dict) -> int:
    daten = datenordner(cfg)
    info = json_lesen(basisordner(cfg) / INFO_DATEI)
    if not info:
        meldung(f"Kein Server gefunden.\nDatenordner: {daten}")
    else:
        zustand = "läuft" if info_aktuell(info) else "antwortet nicht (veraltetes Lebenszeichen)"
        meldung(f"Server auf PC „{info.get('pc')}“ {zustand}.\nAdresse: {info.get('url')}\n"
                f"Gestartet: {info.get('gestartet')}\nLebenszeichen: {info.get('lebenszeichen')}\n"
                f"Datenordner: {daten}")
    return 0


def main(argv: list[str]) -> int:
    cfg = einstellungen()
    if "--beenden" in argv:
        return beenden()
    if "--status" in argv:
        return status(cfg)
    if "--arbeitsplatz" in argv or ("--server" not in argv and cfg.get("modus") == "arbeitsplatz"):
        return arbeitsplatz(cfg)
    return server_starten(cfg, ohne_browser="--ohne-browser" in argv)


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except SystemExit:
        raise
    except Exception:
        text = traceback.format_exc()
        try:
            log.error(text)
        finally:
            meldung(f"{PROGRAMM} (Netzwerk) konnte nicht gestartet werden.\n\n{text[-1500:]}", fehler=True)
        sys.exit(1)
