"""Datensicherung: alle Daten und Dokumente in eine ZIP-Datei – auf ein beliebiges Laufwerk oder als Download.

Inhalt der Sicherung
- die Datenbank (Kunden, Angebote, Rechnungen, Belege, Benutzer, Protokoll) als konsistenter Stand:
  Während die Datei kopiert wird, hält eine Lesetransaktion alle Schreibzugriffe kurz an;
- alle Dokumente im Datenordner: Belegdateien, Rechnungs-PDF/-XML, Angebote, Logos, Kundenordner;
- sicherung.json (Programmversion, Zeitpunkt, Liste aller Dateien mit SHA-256) und LIESMICH.txt.

Nicht gesichert werden nur Hilfsdateien, die das Programm selbst neu erzeugt (Sitzungsschlüssel,
Sperr- und Zwischendateien, Startprotokolle).

Mit Passwort wird die ZIP-Datei mit AES-256 verschlüsselt (WinZip-AES). Öffnen lässt sie sich mit
7-Zip, WinRAR, WinZip oder macOS-Archivprogrammen wie Keka – nicht mit dem in Windows eingebauten
ZIP-Ordner. Ohne Passwort entsteht eine normale ZIP-Datei.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import platform
import shutil
import sqlite3
import string
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .. import PROGRAMM_NAME, __version__, db

PASSWORT_MIN = 8
STANDARD_ORDNER = "Buchhaltung-Sicherung"
MANIFEST = "sicherung.json"
LIESMICH = "LIESMICH.txt"
# Hilfsdateien, die nicht in die Sicherung gehören
AUSSCHLUSS_NAMEN = {".secret_key", "einrichtungscode.txt", "netzwerk-server.sperre", "netzwerk-server.json",
                    "Thumbs.db", "desktop.ini", ".DS_Store"}
AUSSCHLUSS_ENDUNGEN = (".tmp", ".teil", "-journal", "-wal", "-shm")
AUSSCHLUSS_ORDNER = {"protokoll", "__pycache__"}


class SicherungsFehler(Exception):
    pass


@dataclass
class Ergebnis:
    pfad: Path
    groesse: int
    anzahl: int
    verschluesselt: bool
    sekunden: float


# ---------------------------------------------------------------------------
# Ziele
# ---------------------------------------------------------------------------

_LAUFWERKSARTEN = {2: "Wechseldatenträger", 3: "Festplatte", 4: "Netzlaufwerk", 6: "RAM-Disk"}


def laufwerke() -> list[dict]:
    """Verfügbare Laufwerke für das Sicherungsziel (Windows: alle Buchstaben außer CD/DVD;
    sonst Basisordner, eingehängte Datenträger und das Benutzerverzeichnis)."""
    ergebnis: list[dict] = []
    if os.name == "nt":
        k32 = ctypes.windll.kernel32
        k32.SetErrorMode(0x0001 | 0x8000)                  # keine „Kein Datenträger“-Dialoge
        maske = k32.GetLogicalDrives()
        for i, buchstabe in enumerate(string.ascii_uppercase):
            if not maske & (1 << i):
                continue
            wurzel = f"{buchstabe}:\\"
            art = k32.GetDriveTypeW(wurzel)
            if art not in _LAUFWERKSARTEN:
                continue
            name = ctypes.create_unicode_buffer(261)
            if not k32.GetVolumeInformationW(wurzel, name, 261, None, None, None, None, 0):
                continue                                   # z. B. Kartenleser ohne Karte
            ergebnis.append(_eintrag(wurzel, name.value, _LAUFWERKSARTEN[art]))
    else:
        benutzer = os.environ.get("USER", "")
        kandidaten = [Path.home()]
        for basis in (Path("/media") / benutzer, Path("/run/media") / benutzer, Path("/media"), Path("/mnt"),
                      Path("/Volumes")):
            if basis.is_dir() and basis != Path("/media") / "":
                kandidaten += sorted(p for p in basis.iterdir() if p.is_dir())
        for p in dict.fromkeys(kandidaten):
            if os.access(p, os.W_OK):
                ergebnis.append(_eintrag(str(p), p.name, "Ordner"))
    return [e for e in ergebnis if e is not None]


def _eintrag(pfad: str, name: str, art: str) -> dict | None:
    try:
        frei = shutil.disk_usage(pfad).free
    except OSError:
        return None
    return {"pfad": pfad, "name": name, "art": art, "frei": frei}


def zielordner(laufwerk: str, ordner: str, eigener_pfad: str = "") -> Path:
    """Ziel aus Laufwerk + Unterordner oder einem vollständig eingegebenen Pfad."""
    if eigener_pfad.strip():
        ziel = Path(os.path.expandvars(os.path.expanduser(eigener_pfad.strip().strip('"'))))
        if not ziel.is_absolute():
            raise SicherungsFehler("Bitte einen vollständigen Pfad angeben, z. B. E:\\Sicherung oder \\\\NAS\\Sicherung.")
        return ziel
    if not laufwerk:
        raise SicherungsFehler("Bitte ein Laufwerk wählen oder einen Pfad eingeben.")
    teile = [t for t in ordner.replace("\\", "/").split("/") if t.strip()]
    if any(t.strip() in (".", "..") for t in teile):
        raise SicherungsFehler("Der Unterordner darf keine Angaben wie „..“ enthalten.")
    return Path(laufwerk).joinpath(*[t.strip() for t in teile])


def dateiname(jetzt: datetime | None = None) -> str:
    return f"Buchhaltung-Sicherung_{(jetzt or datetime.now()):%Y-%m-%d_%H%M}.zip"


# ---------------------------------------------------------------------------
# Inhalt
# ---------------------------------------------------------------------------

def zu_sichern(daten: Path, datenbank: Path) -> list[Path]:
    """Alle Dateien im Datenordner außer Datenbank (wird gesondert gesichert) und Hilfsdateien."""
    dateien = []
    for wurzel, ordner, namen in os.walk(daten):
        ordner[:] = sorted(o for o in ordner if o not in AUSSCHLUSS_ORDNER and not o.startswith(".sicherung"))
        for n in sorted(namen):
            p = Path(wurzel) / n
            if (p == datenbank or n in AUSSCHLUSS_NAMEN or n.endswith(AUSSCHLUSS_ENDUNGEN)
                    or n.startswith(".schreibtest") or not p.is_file()):
                continue
            dateien.append(p)
    return dateien


def _sha256(pfad: Path) -> str:
    h = hashlib.sha256()
    with open(pfad, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _liesmich(verschluesselte_db: bool, mit_passwort: bool) -> str:
    zeilen = [
        f"Datensicherung von {PROGRAMM_NAME} {__version__}",
        f"Erstellt am {datetime.now():%d.%m.%Y um %H:%M} Uhr auf {platform.node()}",
        "",
        "Inhalt: der vollständige Datenordner – Datenbank (xrechnung.db) mit Kunden, Angeboten,",
        "Rechnungen, Belegen und Änderungsprotokoll sowie alle Dokumente (Belegdateien, PDF, XRechnung).",
        "sicherung.json enthält eine Liste aller Dateien mit SHA-256-Prüfsumme.",
        "",
        "Wiederherstellen:",
        "  1. Buchhaltung für KMU beenden (Netzwerkversion: Server beenden).",
        "  2. Den bisherigen Datenordner umbenennen (nicht löschen), z. B. in „… alt“.",
        "  3. Den Inhalt dieser ZIP-Datei in einen neuen, leeren Datenordner mit dem bisherigen Namen entpacken",
        "     (LIESMICH.txt und sicherung.json können weggelassen werden).",
        "  4. Programm starten und die Daten prüfen.",
    ]
    if mit_passwort:
        zeilen += ["", "Diese ZIP-Datei ist mit AES-256 verschlüsselt. Zum Entpacken 7-Zip, WinRAR oder WinZip",
                   "verwenden – der in Windows eingebaute ZIP-Ordner kann AES-verschlüsselte Dateien nicht öffnen."]
    if verschluesselte_db:
        zeilen += ["", "Die Datenbank selbst ist zusätzlich verschlüsselt (SQLCipher). Nach dem Wiederherstellen",
                   "wird beim Start das bisherige Datenbank-Passwort benötigt."]
    zeilen += ["", "Buchungsbelege sind 10 Jahre aufzubewahren (§ 147 AO) – die Sicherung bitte entsprechend aufheben."]
    return "\r\n".join(zeilen) + "\r\n"


def _zip_oeffnen(pfad: Path, passwort: str | None):
    if passwort:
        import pyzipper

        z = pyzipper.AESZipFile(pfad, "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES)
        z.setpassword(passwort.encode("utf-8"))
        z.setencryption(pyzipper.WZ_AES, nbits=256)
        return z
    return zipfile.ZipFile(pfad, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6)


def _zip_lesen(pfad: Path, passwort: str | None):
    if passwort:
        import pyzipper

        z = pyzipper.AESZipFile(pfad)
        z.setpassword(passwort.encode("utf-8"))
        return z
    return zipfile.ZipFile(pfad)


def passwort_pruefen(passwort: str, wiederholung: str) -> str | None:
    if not passwort and not wiederholung:
        return None
    if len(passwort) < PASSWORT_MIN:
        raise SicherungsFehler(f"Das Passwort für die Sicherung muss mindestens {PASSWORT_MIN} Zeichen lang sein.")
    if passwort != wiederholung:
        raise SicherungsFehler("Die beiden Passwörter stimmen nicht überein.")
    return passwort


# ---------------------------------------------------------------------------
# Erstellen
# ---------------------------------------------------------------------------

def erstellen(daten: Path, datenbank: Path, db_passwort: str | None, ziel: Path,
              passwort: str | None = None, *, name: str | None = None) -> Ergebnis:
    """Schreibt die Sicherung nach ziel/<name>. Die Datei entsteht zunächst als .teil und wird erst nach
    erfolgreicher Prüfung umbenannt – eine abgebrochene Sicherung hinterlässt keine halbe ZIP-Datei."""
    beginn = time.monotonic()
    daten, ziel = Path(daten).resolve(), Path(ziel)
    try:
        ziel_echt = ziel.resolve()
    except OSError:
        ziel_echt = ziel
    if ziel_echt == daten or daten in ziel_echt.parents:
        raise SicherungsFehler("Die Sicherung darf nicht im Datenordner selbst liegen – bitte ein anderes "
                               "Laufwerk wählen (am besten einen USB-Stick oder eine externe Festplatte).")
    try:
        ziel.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise SicherungsFehler(f"Der Zielordner {ziel} kann nicht angelegt werden: {e.strerror or e}") from e

    dateien = zu_sichern(daten, datenbank)
    benoetigt = sum(p.stat().st_size for p in dateien) + (datenbank.stat().st_size if datenbank.exists() else 0)
    try:
        frei = shutil.disk_usage(ziel).free
    except OSError:
        frei = None
    if frei is not None and frei < benoetigt + 10 * 1024 * 1024:
        raise SicherungsFehler(f"Auf dem Ziel ist nicht genug Platz frei ({frei / 1e6:.0f} MB frei, "
                               f"benötigt bis zu {benoetigt / 1e6:.0f} MB).")

    endgueltig = ziel / (name or dateiname())
    if endgueltig.exists():
        endgueltig = endgueltig.with_name(endgueltig.stem + f"_{datetime.now():%S}" + endgueltig.suffix)
    teil = endgueltig.with_name(endgueltig.name + ".teil")
    liste = []
    try:
        with _zip_oeffnen(teil, passwort) as z:
            # 1. Datenbank: Lesetransaktion hält Schreiber an, solange die Datei kopiert wird
            if datenbank.exists():
                con = db.verbinden(datenbank, db_passwort)
                try:
                    con.execute("BEGIN")
                    con.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()
                    z.write(datenbank, datenbank.name)
                    liste.append({"pfad": datenbank.name, "groesse": datenbank.stat().st_size,
                                  "sha256": _sha256(datenbank)})
                finally:
                    con.rollback()
                    con.close()
            # 2. Dokumente
            for p in dateien:
                rel = p.relative_to(daten).as_posix()
                try:
                    z.write(p, rel)
                except OSError as e:
                    raise SicherungsFehler(f"Datei konnte nicht gelesen werden: {rel} ({e.strerror or e})") from e
                liste.append({"pfad": rel, "groesse": p.stat().st_size, "sha256": _sha256(p)})
            manifest = {"programm": PROGRAMM_NAME, "version": __version__,
                        "erstellt": datetime.now().replace(microsecond=0).isoformat(),
                        "rechner": platform.node(), "datenbank_verschluesselt": bool(db_passwort),
                        "zip_verschluesselt": bool(passwort), "anzahl": len(liste),
                        "groesse": sum(x["groesse"] for x in liste), "dateien": liste}
            z.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=1))
            z.writestr(LIESMICH, _liesmich(bool(db_passwort), bool(passwort)).encode("utf-8"))
        pruefen(teil, passwort, erwartet=len(liste))
        os.replace(teil, endgueltig)
    except SicherungsFehler:
        teil.unlink(missing_ok=True)
        raise
    except (OSError, sqlite3.Error, zipfile.BadZipFile, RuntimeError) as e:
        teil.unlink(missing_ok=True)
        raise SicherungsFehler(f"Die Sicherung ist fehlgeschlagen: {e}") from e
    return Ergebnis(endgueltig, endgueltig.stat().st_size, len(liste), bool(passwort),
                    round(time.monotonic() - beginn, 1))


def pruefen(pfad: Path, passwort: str | None = None, erwartet: int | None = None) -> dict:
    """Liest die ZIP-Datei vollständig zurück (CRC bzw. AES-Prüfung jeder Datei) und vergleicht mit sicherung.json."""
    with _zip_lesen(pfad, passwort) as z:
        kaputt = z.testzip()
        if kaputt:
            raise SicherungsFehler(f"Die Sicherung ist fehlerhaft (Datei {kaputt}).")
        manifest = json.loads(z.read(MANIFEST).decode("utf-8"))
        namen = set(z.namelist())
    fehlt = [d["pfad"] for d in manifest["dateien"] if d["pfad"] not in namen]
    if fehlt or (erwartet is not None and manifest["anzahl"] != erwartet):
        raise SicherungsFehler(f"Die Sicherung ist unvollständig ({len(fehlt)} Dateien fehlen).")
    return manifest


def groesse_text(n: int) -> str:
    for einheit in ("Byte", "KB", "MB", "GB"):
        if n < 1024 or einheit == "GB":
            return (f"{n:.0f} {einheit}" if einheit == "Byte" else f"{n:.1f} {einheit}").replace(".", ",")
        n /= 1024
    return str(n)
