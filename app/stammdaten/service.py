"""Fachlogik der Firmenstammdaten inkl. Logo.

Die Stammdaten sind die Verkäuferangaben auf Angeboten und Rechnungen.
Es gibt genau einen Datensatz (id = 1).
"""

from __future__ import annotations

import io
import re
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from .. import protokoll

BEREICH = "stammdaten"

TEXTFELDER = (
    "firma", "handelsname", "inhaber",
    "strasse", "adresszusatz", "plz", "ort", "land",
    "telefon", "email", "website", "elektronische_adresse",
    "kontakt_name", "kontakt_telefon", "kontakt_email",
    "ust_id", "steuernummer", "finanzamt", "registergericht", "registernummer",
    "kontoinhaber", "iban", "bic", "bank",
)

BEZEICHNUNG = {
    "firma": "Firmenname", "handelsname": "Handelsname", "inhaber": "Inhaber / Geschäftsführung",
    "strasse": "Straße und Hausnummer", "adresszusatz": "Adresszusatz", "plz": "PLZ", "ort": "Ort",
    "land": "Land", "telefon": "Telefon", "email": "E-Mail", "website": "Website",
    "elektronische_adresse": "Elektronische Adresse",
    "kontakt_name": "Kontaktperson", "kontakt_telefon": "Kontakt-Telefon", "kontakt_email": "Kontakt-E-Mail",
    "ust_id": "USt-IdNr.", "steuernummer": "Steuernummer", "finanzamt": "Finanzamt",
    "registergericht": "Registergericht", "registernummer": "Registernummer",
    "kleinunternehmer": "Kleinunternehmer (§ 19 UStG)",
    "kontoinhaber": "Kontoinhaber", "iban": "IBAN", "bic": "BIC", "bank": "Bank",
    "zahlungsziel_tage": "Zahlungsziel (Tage)",
}

# Logo
LOGO_MAX_BYTES = 5 * 1024 * 1024
LOGO_MAX_KANTE = 1200            # längste Kante nach dem Verkleinern (px)
LOGO_MAX_PIXEL = 40_000_000      # Schutz vor übergroßen Bildern
LOGO_FORMATE = {"PNG", "JPEG", "WEBP"}

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_BIC = re.compile(r"^[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}([A-Z0-9]{3})?$")
_UST_DE = re.compile(r"^DE\d{9}$")
_UST_EU = re.compile(r"^[A-Z]{2}[A-Z0-9+*]{2,12}$")


class StammdatenFehler(Exception):
    """Fachlicher Fehler, der dem Anwender angezeigt werden kann."""


class ValidierungsFehler(StammdatenFehler):
    def __init__(self, fehler: dict[str, str]):
        super().__init__("Bitte die markierten Felder prüfen.")
        self.fehler = fehler


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def iban_normalisieren(iban: str) -> str:
    return re.sub(r"\s+", "", iban or "").upper()


def iban_gueltig(iban: str) -> bool:
    iban = iban_normalisieren(iban)
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}", iban):
        return False
    if iban.startswith("DE") and len(iban) != 22:
        return False
    umgestellt = iban[4:] + iban[:4]
    zahl = "".join(str(int(c, 36)) for c in umgestellt)
    return int(zahl) % 97 == 1


def iban_formatiert(iban: str) -> str:
    iban = iban_normalisieren(iban)
    return " ".join(iban[i:i + 4] for i in range(0, len(iban), 4))


# ---------------------------------------------------------------------------
# Lesen
# ---------------------------------------------------------------------------

def laden(con: sqlite3.Connection) -> sqlite3.Row:
    zeile = con.execute("SELECT * FROM stammdaten WHERE id = 1").fetchone()
    if zeile is None:  # sollte durch das Schema nie passieren
        con.execute("INSERT OR IGNORE INTO stammdaten (id) VALUES (1)")
        con.commit()
        zeile = con.execute("SELECT * FROM stammdaten WHERE id = 1").fetchone()
    return zeile


def verlauf(con: sqlite3.Connection, limit: int | None = 50) -> list[dict]:
    return protokoll.lesen(con, BEREICH, 1, limit)


@dataclass
class Pflichtangabe:
    feld: str
    text: str


def fehlende_pflichtangaben(daten, logo_vorhanden: bool | None = None) -> list[Pflichtangabe]:
    """Angaben, die für eine gültige XRechnung bzw. eine Rechnung nach § 14 UStG fehlen.

    Wird später auch vom Rechnungsmodul vor dem Erzeugen geprüft.
    """
    fehlt: list[Pflichtangabe] = []

    def pruefe(feld: str, text: str) -> None:
        if not str(daten[feld] or "").strip():
            fehlt.append(Pflichtangabe(feld, text))

    pruefe("firma", "Firmenname")
    pruefe("strasse", "Straße und Hausnummer")
    pruefe("plz", "Postleitzahl")
    pruefe("ort", "Ort")
    if not (daten["ust_id"] or "").strip() and not (daten["steuernummer"] or "").strip():
        fehlt.append(Pflichtangabe("ust_id", "USt-IdNr. oder Steuernummer"))
    pruefe("elektronische_adresse", "Elektronische Adresse (BT-34)")
    pruefe("kontakt_name", "Kontaktperson")
    pruefe("kontakt_telefon", "Telefon der Kontaktperson")
    pruefe("kontakt_email", "E-Mail der Kontaktperson")
    pruefe("iban", "IBAN für die Zahlung per Überweisung")
    return fehlt


# ---------------------------------------------------------------------------
# Validierung & Speichern
# ---------------------------------------------------------------------------

def aus_formular(form) -> dict:
    daten = {f: (form.get(f) or "").strip() for f in TEXTFELDER}
    daten["land"] = daten["land"].upper() or "DE"
    daten["ust_id"] = re.sub(r"\s+", "", daten["ust_id"]).upper()
    daten["iban"] = iban_normalisieren(daten["iban"])
    daten["bic"] = re.sub(r"\s+", "", daten["bic"]).upper()
    daten["kleinunternehmer"] = 1 if form.get("kleinunternehmer") else 0
    roh = (form.get("zahlungsziel_tage") or "").strip()
    daten["zahlungsziel_tage"] = int(roh) if roh.isdigit() else roh
    return daten


def pruefen(d: dict) -> dict[str, str]:
    f: dict[str, str] = {}
    if not d["firma"]:
        f["firma"] = "Bitte den Firmennamen angeben."
    if not re.fullmatch(r"[A-Z]{2}", d["land"]):
        f["land"] = "Ländercode mit zwei Großbuchstaben angeben (z. B. DE)."
    if d["land"] == "DE" and d["plz"] and not re.fullmatch(r"\d{5}", d["plz"]):
        f["plz"] = "Deutsche Postleitzahlen haben fünf Ziffern."
    for feld in ("email", "kontakt_email"):
        if d[feld] and not _EMAIL.match(d[feld]):
            f[feld] = "Die E-Mail-Adresse ist ungültig."
    if d["elektronische_adresse"] and not _EMAIL.match(d["elektronische_adresse"]):
        f["elektronische_adresse"] = "Bitte eine E-Mail-Adresse für den Rechnungsversand angeben."
    if d["ust_id"]:
        if d["ust_id"].startswith("DE") and not _UST_DE.match(d["ust_id"]):
            f["ust_id"] = "Deutsche USt-IdNr.: DE gefolgt von 9 Ziffern."
        elif not _UST_EU.match(d["ust_id"]):
            f["ust_id"] = "Format der USt-IdNr. ungültig."
    if d["iban"] and not iban_gueltig(d["iban"]):
        f["iban"] = "Die IBAN ist ungültig (Prüfziffer oder Länge stimmt nicht)."
    if d["bic"] and not _BIC.match(d["bic"]):
        f["bic"] = "Die BIC hat 8 oder 11 Zeichen."
    if not isinstance(d["zahlungsziel_tage"], int) or not 0 <= d["zahlungsziel_tage"] <= 365:
        f["zahlungsziel_tage"] = "Bitte eine Zahl zwischen 0 und 365 angeben."
    if d["website"] and not re.match(r"^(https?://)?[^\s/]+\.[^\s]+$", d["website"]):
        f["website"] = "Die Website-Adresse ist ungültig."
    return f


def speichern(con: sqlite3.Connection, form) -> bool:
    """Speichert die Stammdaten. Liefert False, wenn sich nichts geändert hat."""
    neu = aus_formular(form)
    fehler = pruefen(neu)
    if fehler:
        raise ValidierungsFehler(fehler)

    alt = laden(con)
    diff = protokoll.felder_diff(alt, neu, BEZEICHNUNG)
    if not diff:
        return False
    # Bool-Werte im Protokoll lesbar darstellen
    if BEZEICHNUNG["kleinunternehmer"] in diff:
        diff[BEZEICHNUNG["kleinunternehmer"]] = ["ja" if v else "nein" for v in diff[BEZEICHNUNG["kleinunternehmer"]]]

    erstmalig = not alt["geaendert_am"]
    spalten = (*TEXTFELDER, "kleinunternehmer", "zahlungsziel_tage")
    with con:
        con.execute(
            f"UPDATE stammdaten SET {', '.join(s + ' = ?' for s in spalten)}, geaendert_am = ? WHERE id = 1",
            [*(neu[s] for s in spalten), protokoll.jetzt()],
        )
        protokoll.schreiben(con, BEREICH, 1, "angelegt" if erstmalig else "geaendert", {"felder": diff})
    return True


# ---------------------------------------------------------------------------
# Logo
# ---------------------------------------------------------------------------

def logo_pfad(datenverzeichnis: Path) -> Path:
    return Path(datenverzeichnis) / "stammdaten" / "logo.png"


def logo_vorhanden(datenverzeichnis: Path) -> bool:
    return logo_pfad(datenverzeichnis).is_file()


def _logo_archivieren(datenverzeichnis: Path) -> str | None:
    """Verschiebt das bisherige Logo ins Archiv, damit ältere Belege nachvollziehbar bleiben."""
    pfad = logo_pfad(datenverzeichnis)
    if not pfad.is_file():
        return None
    archiv = pfad.parent / "logo_archiv"
    archiv.mkdir(parents=True, exist_ok=True)
    ziel = archiv / f"logo_{datetime.now():%Y%m%d_%H%M%S_%f}.png"
    shutil.move(str(pfad), ziel)
    return ziel.name


def logo_aufbereiten(rohdaten: bytes) -> tuple[bytes, tuple[int, int]]:
    """Prüft das hochgeladene Bild und speichert es einheitlich als PNG.

    Das Neu-Kodieren entfernt Metadaten (z. B. EXIF) und stellt sicher,
    dass nur echte Bilddaten abgelegt werden.
    """
    if not rohdaten:
        raise StammdatenFehler("Bitte eine Bilddatei auswählen.")
    if len(rohdaten) > LOGO_MAX_BYTES:
        raise StammdatenFehler("Das Logo darf höchstens 5 MB groß sein.")
    try:
        with Image.open(io.BytesIO(rohdaten)) as bild:
            if bild.format not in LOGO_FORMATE:
                raise StammdatenFehler("Erlaubt sind PNG, JPG oder WebP.")
            if bild.width * bild.height > LOGO_MAX_PIXEL:
                raise StammdatenFehler("Das Bild hat zu viele Pixel.")
            bild = ImageOps.exif_transpose(bild)
            bild = bild.convert("RGBA")
            bild.thumbnail((LOGO_MAX_KANTE, LOGO_MAX_KANTE), Image.LANCZOS)
            # Leere (transparente) Ränder abschneiden
            rahmen = bild.getchannel("A").getbbox()
            if rahmen:
                bild = bild.crop(rahmen)
            ausgabe = io.BytesIO()
            bild.save(ausgabe, format="PNG", optimize=True)
            return ausgabe.getvalue(), bild.size
    except StammdatenFehler:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, ValueError):
        raise StammdatenFehler("Die Datei konnte nicht als Bild gelesen werden.") from None


def logo_speichern(con: sqlite3.Connection, datenverzeichnis: Path, rohdaten: bytes,
                   dateiname: str = "") -> tuple[int, int]:
    png, groesse = logo_aufbereiten(rohdaten)
    pfad = logo_pfad(datenverzeichnis)
    pfad.parent.mkdir(parents=True, exist_ok=True)
    archiviert = _logo_archivieren(datenverzeichnis)
    tmp = pfad.with_suffix(".tmp")
    tmp.write_bytes(png)
    tmp.replace(pfad)
    with con:
        con.execute("UPDATE stammdaten SET logo_aktualisiert_am = ? WHERE id = 1", (protokoll.jetzt(),))
        hinweise = [f"Logo hochgeladen: {dateiname or 'Bild'} ({groesse[0]} × {groesse[1]} px)"]
        if archiviert:
            hinweise.append(f"Bisheriges Logo archiviert als {archiviert}")
        protokoll.schreiben(con, BEREICH, 1, "logo", {"hinweise": hinweise})
    return groesse


def logo_entfernen(con: sqlite3.Connection, datenverzeichnis: Path) -> None:
    archiviert = _logo_archivieren(datenverzeichnis)
    if not archiviert:
        raise StammdatenFehler("Es ist kein Logo hinterlegt.")
    with con:
        con.execute("UPDATE stammdaten SET logo_aktualisiert_am = NULL WHERE id = 1")
        protokoll.schreiben(con, BEREICH, 1, "logo",
                            {"hinweise": [f"Logo entfernt (archiviert als {archiviert})"]})
