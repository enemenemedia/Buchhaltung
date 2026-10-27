"""Lizenzprüfung.

Eine Lizenznummer ist eine digitale Signatur (Ed25519) über den Lizenznehmer und einige
Angaben (Seriennummer, Ausstellungsdatum, optional „gültig bis“). Erzeugen kann sie nur,
wer den privaten Schlüssel hat (werkzeuge/lizenz_erzeugen.py); das Programm enthält nur
den öffentlichen Schlüssel und prüft offline.

Aufbau:  XRK1-AAAAA-BBBBB-…   (Base32, Groß-/Kleinschreibung und Bindestriche egal)
  Inhalt (10 Byte): Version 1 · Typ 1 · Seriennummer 4 · ausgestellt 2 · gültig bis 2
                    (Datumsangaben als Tage seit 01.01.2000, gültig bis 0 = unbegrenzt)
  Signatur (64 Byte) über b"XRK1" + Inhalt + Lizenznehmer (normalisiert, UTF-8)

Ohne gültige Lizenz läuft das Programm als Testversion: alles ist nutzbar, nur die
XRechnung (XML) wird nicht erstellt. Die Daten bleiben immer zugänglich.
"""

from __future__ import annotations

import base64
import configparser
import struct
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

# Öffentlicher Schlüssel (Ed25519, Base64). Der zugehörige private Schlüssel gehört NICHT
# ins Repository. Neues Schlüsselpaar: python werkzeuge/lizenz_erzeugen.py schluessel …
OEFFENTLICHER_SCHLUESSEL = "D/7+29Y9pyVaUnUB0gDzXdDmVI+GsQaqVeo+GtBNpPc="

PRAEFIX = "XRK1"
DATEI = "lizenz.ini"
EPOCHE = date(2000, 1, 1)
TYP_VOLL = 1
TYPEN = {TYP_VOLL: "Vollversion"}
_INHALT = struct.Struct(">BBIHH")   # Version, Typ, Seriennummer, ausgestellt, gültig bis
_SIGNATUR = 64


class LizenzFehler(ValueError):
    pass


@dataclass(frozen=True)
class Lizenz:
    lizenznehmer: str
    seriennummer: int
    typ: int
    ausgestellt: date
    gueltig_bis: date | None
    nummer: str

    def gueltig(self, heute: date | None = None) -> bool:
        return self.gueltig_bis is None or (heute or date.today()) <= self.gueltig_bis

    @property
    def typ_name(self) -> str:
        return TYPEN.get(self.typ, "Lizenz")


def name_normalisieren(name: str) -> str:
    """Leerzeichen und Groß-/Kleinschreibung spielen beim Lizenznehmer keine Rolle."""
    return " ".join(unicodedata.normalize("NFC", name or "").split()).casefold()


def _nachricht(inhalt: bytes, name: str) -> bytes:
    return PRAEFIX.encode() + inhalt + name_normalisieren(name).encode("utf-8")


def _tage(d: date) -> int:
    return (d - EPOCHE).days


def formatieren(roh: bytes) -> str:
    text = base64.b32encode(roh).decode().rstrip("=")
    return "-".join([PRAEFIX] + [text[i:i + 5] for i in range(0, len(text), 5)])


def _dekodieren(nummer: str) -> bytes:
    text = "".join(ch for ch in (nummer or "").upper() if ch.isalnum())
    if not text.startswith(PRAEFIX):
        raise LizenzFehler("Die Lizenznummer muss mit „XRK1-“ beginnen.")
    text = text[len(PRAEFIX):]
    # Verwechslungen beim Abtippen (Base32 kennt weder 0, 1, 8 noch 9)
    text = text.translate(str.maketrans({"0": "O", "1": "I", "8": "B"}))
    try:
        roh = base64.b32decode(text + "=" * (-len(text) % 8))
    except ValueError:
        raise LizenzFehler("Die Lizenznummer enthält ungültige Zeichen.") from None
    if len(roh) != _INHALT.size + _SIGNATUR:
        raise LizenzFehler("Die Lizenznummer ist unvollständig. Bitte vollständig aus der E-Mail kopieren.")
    return roh


def _oeffentlicher_schluessel(schluessel: str | None):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    text = schluessel if schluessel is not None else OEFFENTLICHER_SCHLUESSEL
    if not text:
        raise LizenzFehler("In dieser Programmversion ist kein Lizenzschlüssel hinterlegt.")
    return Ed25519PublicKey.from_public_bytes(base64.b64decode(text))


def pruefen(lizenznehmer: str, nummer: str, *, heute: date | None = None,
            oeffentlicher_schluessel: str | None = None) -> Lizenz:
    """Prüft Lizenznehmer und Lizenznummer. Gibt die Lizenz zurück oder löst LizenzFehler aus."""
    from cryptography.exceptions import InvalidSignature

    if not name_normalisieren(lizenznehmer):
        raise LizenzFehler("Bitte den Lizenznehmer genau so eingeben, wie er in der Lizenz-E-Mail steht.")
    roh = _dekodieren(nummer)
    inhalt, signatur = roh[:_INHALT.size], roh[_INHALT.size:]
    try:
        _oeffentlicher_schluessel(oeffentlicher_schluessel).verify(signatur, _nachricht(inhalt, lizenznehmer))
    except InvalidSignature:
        raise LizenzFehler("Lizenznehmer und Lizenznummer passen nicht zusammen. Bitte beides genau so "
                           "eingeben, wie es in der Lizenz-E-Mail steht.") from None
    version, typ, serie, ausgestellt, bis = _INHALT.unpack(inhalt)
    if version != 1 or typ not in TYPEN:
        raise LizenzFehler("Diese Lizenznummer gehört zu einer anderen Programmversion.")
    lizenz = Lizenz(lizenznehmer=" ".join(lizenznehmer.split()), seriennummer=serie, typ=typ,
                    ausgestellt=EPOCHE + timedelta(days=ausgestellt),
                    gueltig_bis=EPOCHE + timedelta(days=bis) if bis else None, nummer=formatieren(roh))
    if not lizenz.gueltig(heute):
        raise LizenzFehler(f"Die Lizenz ist am {lizenz.gueltig_bis:%d.%m.%Y} abgelaufen.")
    return lizenz


def erzeugen(privater_schluessel, lizenznehmer: str, seriennummer: int, *, gueltig_bis: date | None = None,
             ausgestellt: date | None = None, typ: int = TYP_VOLL) -> str:
    """Erzeugt eine Lizenznummer (nur mit dem privaten Schlüssel, siehe werkzeuge/lizenz_erzeugen.py)."""
    if not name_normalisieren(lizenznehmer):
        raise LizenzFehler("Lizenznehmer fehlt.")
    if not 0 < seriennummer < 2 ** 32:
        raise LizenzFehler("Die Seriennummer muss zwischen 1 und 4294967295 liegen.")
    inhalt = _INHALT.pack(1, typ, seriennummer, _tage(ausgestellt or date.today()),
                          _tage(gueltig_bis) if gueltig_bis else 0)
    return formatieren(inhalt + privater_schluessel.sign(_nachricht(inhalt, lizenznehmer)))


# ---------------------------------------------------------------------------
# Ablage im Datenordner (lizenz.ini) – schreibt auch der Installer
# ---------------------------------------------------------------------------

def datei_lesen(pfad: Path) -> tuple[str, str]:
    cfg = configparser.ConfigParser(interpolation=None)
    try:
        cfg.read(pfad, encoding="utf-8-sig")
    except (configparser.Error, UnicodeDecodeError):
        return "", ""
    return (cfg.get("Lizenz", "lizenznehmer", fallback="").strip(),
            cfg.get("Lizenz", "lizenznummer", fallback="").strip())


def laden(datenverzeichnis: Path, **kw) -> tuple[Lizenz | None, str]:
    """(Lizenz, Fehlermeldung). Ohne Lizenzdatei: (None, '')."""
    pfad = Path(datenverzeichnis) / DATEI
    if not pfad.is_file():
        return None, ""
    name, nummer = datei_lesen(pfad)
    if not name and not nummer:
        return None, ""
    try:
        return pruefen(name, nummer, **kw), ""
    except LizenzFehler as e:
        return None, str(e)


def speichern(datenverzeichnis: Path, lizenz: Lizenz) -> None:
    cfg = configparser.ConfigParser(interpolation=None)
    cfg["Lizenz"] = {"lizenznehmer": lizenz.lizenznehmer, "lizenznummer": lizenz.nummer}
    pfad = Path(datenverzeichnis) / DATEI
    neu = pfad.with_suffix(".neu")
    with neu.open("w", encoding="utf-8") as f:
        cfg.write(f)
    neu.replace(pfad)
