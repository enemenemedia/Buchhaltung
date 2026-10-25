"""Import und Export des Leistungsverzeichnisses (CSV und Excel).

Ablauf des Imports:
  1. Datei einlesen (CSV oder XLSX) und Spalten anhand der Überschriften zuordnen
  2. Vorschau berechnen: jede Zeile wird geprüft und mit dem Bestand verglichen
  3. Nach Bestätigung werden alle gültigen Zeilen in einer Transaktion übernommen
"""

from __future__ import annotations

import csv
import io
import json
import re
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from .. import zahlen
from . import service

MAX_ZEILEN = 5000
IMPORT_AUFBEWAHRUNG_SEK = 24 * 3600

# Zielfeld -> mögliche Spaltenüberschriften (normalisiert: klein, ohne Umlaute/Sonderzeichen)
SPALTEN = {
    "nummer": {"nummer", "nr", "artikelnummer", "artikelnr", "artnr", "leistungsnummer", "leistungsnr",
               "positionsnummer", "posnr", "sku", "code"},
    "bezeichnung": {"bezeichnung", "leistung", "name", "titel", "artikel", "kurztext",
                    "artikelbezeichnung", "leistungsbezeichnung", "produkt"},
    "beschreibung": {"beschreibung", "langtext", "details", "text", "erlaeuterung", "info"},
    "kategorie": {"kategorie", "gruppe", "warengruppe", "leistungsgruppe", "bereich", "rubrik"},
    "art": {"art", "typ", "abrechnungsart", "leistungsart", "abrechnung"},
    "einheit": {"einheit", "me", "mengeneinheit", "unit", "einheitcode"},
    "preis": {"preis", "einzelpreis", "preisnetto", "nettopreis", "ep", "vkpreis", "verkaufspreis",
              "stundensatz", "satz", "netto", "betrag", "einzelpreisnetto", "price"},
    "anzahl": {"anzahl", "menge", "standardmenge", "stueckzahl", "qty", "quantity"},
    "ust_satz": {"ust", "mwst", "ustsatz", "mwstsatz", "steuersatz", "ustprozent", "mwstprozent",
                 "umsatzsteuer", "steuer", "vat"},
}

# Text in Spalte „Art“ oder „Einheit“ -> Einheitencode
EINHEIT_WOERTER = {
    "HUR": {"stunde", "stunden", "std", "h", "hur", "hour", "hours", "stundensatz", "zeit", "zeiteinheit"},
    "MIN": {"minute", "minuten", "min", "minutes"},
    "DAY": {"tag", "tage", "tg", "day", "days", "tagessatz", "pt", "personentag"},
    "WEE": {"woche", "wochen", "wo", "wee", "week"},
    "MON": {"monat", "monate", "mon", "month", "monatlich"},
    "H87": {"stueck", "stuck", "stk", "st", "h87", "c62", "piece", "pcs", "stck"},
    "LS": {"pauschal", "pauschale", "psch", "pausch", "ls", "pauschalpreis", "festpreis", "lumpsum"},
}
_WORT_ZU_CODE = {w: code for code, woerter in EINHEIT_WOERTER.items() for w in woerter}
_ART_WOERTER = {"zeit": "zeit", "zeiteinheit": "zeit", "stueck": "stueck", "stuck": "stueck",
                "stk": "stueck", "pauschal": "pauschal", "pauschale": "pauschal"}

EXPORT_KOPF = ["Nummer", "Bezeichnung", "Beschreibung", "Kategorie", "Art", "Einheit",
               "Preis netto", "Anzahl", "USt %"]

VORLAGE_ZEILEN = [
    ["L0001", "Beratung", "Beratung vor Ort oder per Video", "Beratung", "Zeiteinheit", "Stunde", "95,00", "1", "19"],
    ["L0002", "Projekttag", "Ganztägige Projektarbeit (8 Std.)", "Beratung", "Zeiteinheit", "Tag", "720,00", "1", "19"],
    ["L0003", "Druck Flyer A5", "Je 100 Stück, 4/4-farbig", "Druck", "Stück", "Stück", "38,50", "5", "19"],
    ["L0004", "Einrichtung Webhosting", "", "Web", "Pauschal", "Pauschale", "149,00", "1", "19"],
]


class ImportFehler(Exception):
    pass


def _norm(text) -> str:
    t = str(text or "").strip().lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        t = t.replace(a, b)
    t = re.sub(r"\b(in|eur|euro)\b", "", t)
    return re.sub(r"[^a-z0-9]", "", t)


# ---------------------------------------------------------------------------
# Datei lesen
# ---------------------------------------------------------------------------

def _csv_lesen(rohdaten: bytes) -> list[list[str]]:
    for kodierung in ("utf-8-sig", "cp1252"):
        try:
            text = rohdaten.decode(kodierung)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover – cp1252 dekodiert praktisch alles
        raise ImportFehler("Die Datei konnte nicht gelesen werden (unbekannte Zeichenkodierung).")
    probe = text[:4096]
    try:
        dialekt = csv.Sniffer().sniff(probe, delimiters=";,\t|")
        trenner = dialekt.delimiter
    except csv.Error:
        trenner = ";" if probe.count(";") >= probe.count(",") else ","
    return [zeile for zeile in csv.reader(io.StringIO(text), delimiter=trenner)]


def _xlsx_lesen(rohdaten: bytes) -> list[list]:
    try:
        mappe = load_workbook(io.BytesIO(rohdaten), read_only=True, data_only=True)
    except Exception:
        raise ImportFehler("Die Excel-Datei konnte nicht geöffnet werden.") from None
    try:
        blatt = mappe.worksheets[0]
        return [list(z) for z in blatt.iter_rows(values_only=True, max_row=MAX_ZEILEN + 50)]
    finally:
        mappe.close()


def tabelle_lesen(dateiname: str, rohdaten: bytes) -> list[list]:
    endung = Path(dateiname or "").suffix.lower()
    if not rohdaten:
        raise ImportFehler("Die Datei ist leer.")
    if endung in (".csv", ".txt"):
        return _csv_lesen(rohdaten)
    if endung in (".xlsx", ".xlsm"):
        return _xlsx_lesen(rohdaten)
    if endung == ".xls":
        raise ImportFehler("Das alte Excel-Format .xls wird nicht unterstützt. "
                           "Bitte in Excel als .xlsx oder .csv speichern.")
    raise ImportFehler("Bitte eine CSV- oder Excel-Datei (.csv, .xlsx) wählen.")


def _zelle(wert) -> str:
    if wert is None:
        return ""
    if isinstance(wert, float):
        # Excel liefert z. B. 95.0 oder 38.5 – ohne wissenschaftliche Schreibweise übernehmen
        return zahlen.speicherform(zahlen.parsen(round(wert, 6)))
    return str(wert).strip()


@dataclass
class Rohdaten:
    dateiname: str
    zuordnung: dict[str, str]                 # Zielfeld -> Originalüberschrift
    ignoriert: list[str]
    zeilen: list[dict] = field(default_factory=list)   # {"zeile": n, "werte": {zielfeld: text}}


def einlesen(dateiname: str, rohdaten: bytes) -> Rohdaten:
    tabelle = tabelle_lesen(dateiname, rohdaten)
    # Erste nicht-leere Zeile ist die Kopfzeile
    start = next((i for i, z in enumerate(tabelle) if any(_zelle(c) for c in z)), None)
    if start is None:
        raise ImportFehler("Die Datei enthält keine Daten.")
    kopf = [_zelle(c) for c in tabelle[start]]

    spalte_zu_feld: dict[int, str] = {}
    zuordnung: dict[str, str] = {}
    ignoriert: list[str] = []
    for i, titel in enumerate(kopf):
        n = _norm(titel)
        ziel = next((f for f, namen in SPALTEN.items() if n in namen), None)
        if ziel is None and n.startswith(("preis", "einzelpreis", "nettopreis")):
            ziel = "preis"
        if ziel and ziel not in zuordnung:
            spalte_zu_feld[i] = ziel
            zuordnung[ziel] = titel
        elif titel:
            ignoriert.append(titel)
    if "bezeichnung" not in zuordnung:
        raise ImportFehler("Keine Spalte „Bezeichnung“ gefunden. Die erste Zeile muss Spaltenüberschriften "
                           "enthalten – siehe Importvorlage.")
    if "preis" not in zuordnung:
        raise ImportFehler("Keine Spalte „Preis“ gefunden. Die erste Zeile muss Spaltenüberschriften "
                           "enthalten – siehe Importvorlage.")

    daten = Rohdaten(dateiname=Path(dateiname).name, zuordnung=zuordnung, ignoriert=ignoriert)
    for nr, zeile in enumerate(tabelle[start + 1:], start=start + 2):
        werte = {feld: _zelle(zeile[i]) if i < len(zeile) else "" for i, feld in spalte_zu_feld.items()}
        if not any(werte.values()):
            continue
        daten.zeilen.append({"zeile": nr, "werte": werte})
        if len(daten.zeilen) > MAX_ZEILEN:
            raise ImportFehler(f"Die Datei enthält mehr als {MAX_ZEILEN} Leistungen.")
    if not daten.zeilen:
        raise ImportFehler("Die Datei enthält nur eine Kopfzeile und keine Leistungen.")
    return daten


# ---------------------------------------------------------------------------
# Zeilen deuten & Vorschau
# ---------------------------------------------------------------------------

def art_und_einheit(art_text: str, einheit_text: str) -> tuple[str, str, str | None]:
    """Leitet Art und Einheitencode aus den Spalten „Art“ und „Einheit“ ab."""
    a, e = _norm(art_text), _norm(einheit_text)
    code = None
    if einheit_text.strip().upper() in service.EINHEIT_NACH_CODE:
        code = einheit_text.strip().upper()
    elif e in _WORT_ZU_CODE:
        code = _WORT_ZU_CODE[e]
    elif e:
        return "", "", f"Einheit „{einheit_text}“ ist unbekannt."

    art = _ART_WOERTER.get(a)
    if art is None and a in _WORT_ZU_CODE:          # z. B. Art = „Stunde“
        code = code or _WORT_ZU_CODE[a]
        art = service.ART_NACH_CODE[_WORT_ZU_CODE[a]]
    elif art is None and a:
        return "", "", f"Art „{art_text}“ ist unbekannt (Zeiteinheit, Stück oder Pauschal)."

    if art is None and code:
        art = service.ART_NACH_CODE[code]
    if art is None:
        return "", "", "Art bzw. Einheit fehlt (Zeiteinheit, Stück oder Pauschal)."
    if code is None:
        code = service.EINHEITEN[art][0].code
    if service.ART_NACH_CODE[code] != art:
        return "", "", f"Einheit „{einheit_text}“ passt nicht zur Art „{art_text}“."
    return art, code, None


@dataclass
class Vorschauzeile:
    zeile: int
    werte: dict
    daten: dict
    fehler: list[str]
    status: str               # neu | aktualisieren | unveraendert | fehler | doppelt
    aenderungen: dict = field(default_factory=dict)
    bestand_id: int | None = None

    def anzeige(self, feld: str) -> str:
        """Preis/Anzahl formatiert – oder der Originaltext, wenn er ungültig ist."""
        try:
            return zahlen.geld(self.daten[feld]) if feld == "preis" else zahlen.menge(self.daten[feld])
        except zahlen.ZahlFehler:
            return self.werte.get(feld, "")


@dataclass
class Vorschau:
    rohdaten: Rohdaten
    zeilen: list[Vorschauzeile]

    def anzahl(self, status: str) -> int:
        return sum(1 for z in self.zeilen if z.status == status)


def vorschau(con, rohdaten: Rohdaten) -> Vorschau:
    ergebnis: list[Vorschauzeile] = []
    gesehen: dict[str, int] = {}
    for eintrag in rohdaten.zeilen:
        w = eintrag["werte"]
        fehler: list[str] = []
        art, code, art_fehler = art_und_einheit(w.get("art", ""), w.get("einheit", ""))
        if art_fehler:
            fehler.append(art_fehler)
        ust = w.get("ust_satz", "") or "19"
        try:  # Excel-Prozentformat liefert 0,19 statt 19
            ust_zahl = zahlen.parsen(ust.replace("%", ""))
            if 0 < ust_zahl < 1:
                ust = zahlen.speicherform(ust_zahl * 100)
        except zahlen.ZahlFehler:
            pass
        roh = {
            "nummer": w.get("nummer", ""), "bezeichnung": w.get("bezeichnung", ""),
            "beschreibung": w.get("beschreibung", ""), "kategorie": w.get("kategorie", ""),
            "art": art, "einheit_code": code, "preis": w.get("preis", ""),
            "anzahl": w.get("anzahl", "") or "1", "ust_satz": ust,
        }
        daten, feldfehler = service.normalisieren(roh)
        fehler += [m for f, m in feldfehler.items() if not (art_fehler and f in ("art", "einheit_code"))]

        zeile = Vorschauzeile(eintrag["zeile"], w, daten, fehler, "fehler" if fehler else "neu")
        nummer = daten["nummer"].upper()
        if nummer and not fehler:
            if nummer in gesehen:
                zeile.status = "doppelt"
                zeile.fehler.append(f"Nummer {daten['nummer']} kommt bereits in Zeile {gesehen[nummer]} vor.")
            else:
                gesehen[nummer] = eintrag["zeile"]
                bestand = service.nach_nummer(con, daten["nummer"])
                if bestand:
                    zeile.bestand_id = bestand["id"]
                    zeile.aenderungen = service.aenderungen_beschreiben(bestand, {**daten, "nummer": bestand["nummer"]})
                    zeile.status = "aktualisieren" if zeile.aenderungen else "unveraendert"
        ergebnis.append(zeile)
    return Vorschau(rohdaten, ergebnis)


@dataclass
class Ergebnis:
    angelegt: int = 0
    aktualisiert: int = 0
    unveraendert: int = 0
    uebersprungen: int = 0
    fehlerhaft: int = 0


def uebernehmen(con, rohdaten: Rohdaten, *, bestehende_aktualisieren: bool) -> Ergebnis:
    """Übernimmt alle gültigen Zeilen in einer Transaktion (alles oder nichts)."""
    v = vorschau(con, rohdaten)
    erg = Ergebnis()
    quelle = rohdaten.dateiname
    with con:
        for z in v.zeilen:
            if z.status in ("fehler", "doppelt"):
                erg.fehlerhaft += 1
            elif z.status == "unveraendert":
                erg.unveraendert += 1
            elif z.status == "aktualisieren":
                if bestehende_aktualisieren:
                    service.aendern(con, z.bestand_id, z.daten, quelle=quelle, commit=False)
                    erg.aktualisiert += 1
                else:
                    erg.uebersprungen += 1
            else:
                service.anlegen(con, z.daten, quelle=quelle, commit=False)
                erg.angelegt += 1
    return erg


# ---------------------------------------------------------------------------
# Zwischenspeicher zwischen Vorschau und Bestätigung
# ---------------------------------------------------------------------------

def _import_ordner(datenverzeichnis: Path) -> Path:
    ordner = Path(datenverzeichnis) / ".import"
    ordner.mkdir(parents=True, exist_ok=True)
    return ordner


def zwischenspeichern(datenverzeichnis: Path, rohdaten: Rohdaten) -> str:
    ordner = _import_ordner(datenverzeichnis)
    grenze = time.time() - IMPORT_AUFBEWAHRUNG_SEK
    for alt in ordner.glob("*.json"):
        if alt.stat().st_mtime < grenze:
            alt.unlink(missing_ok=True)
    token = secrets.token_hex(16)
    (ordner / f"{token}.json").write_text(json.dumps(rohdaten.__dict__, ensure_ascii=False), encoding="utf-8")
    return token


def _token_pfad(datenverzeichnis: Path, token: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", token or ""):
        raise ImportFehler("Der Import ist ungültig oder abgelaufen. Bitte die Datei erneut hochladen.")
    return _import_ordner(datenverzeichnis) / f"{token}.json"


def zwischenspeicher_laden(datenverzeichnis: Path, token: str) -> Rohdaten:
    pfad = _token_pfad(datenverzeichnis, token)
    if not pfad.is_file():
        raise ImportFehler("Der Import ist ungültig oder abgelaufen. Bitte die Datei erneut hochladen.")
    return Rohdaten(**json.loads(pfad.read_text(encoding="utf-8")))


def zwischenspeicher_entfernen(datenverzeichnis: Path, token: str) -> None:
    _token_pfad(datenverzeichnis, token).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Export & Vorlage
# ---------------------------------------------------------------------------

def _export_zeilen(leistungen) -> list[list[str]]:
    return [[
        l["nummer"], l["bezeichnung"], l["beschreibung"], l["kategorie"], service.ARTEN[l["art"]],
        service.einheit_name(l["einheit_code"]), zahlen.geld(l["preis"], mit_waehrung=False),
        zahlen.menge(l["anzahl"]), l["ust_satz"],
    ] for l in leistungen]


def csv_erzeugen(zeilen: list[list[str]]) -> bytes:
    puffer = io.StringIO()
    schreiber = csv.writer(puffer, delimiter=";", lineterminator="\r\n")
    schreiber.writerow(EXPORT_KOPF)
    schreiber.writerows(zeilen)
    # UTF-8 mit BOM, damit Excel Umlaute korrekt anzeigt
    return puffer.getvalue().encode("utf-8-sig")


def xlsx_erzeugen(zeilen: list[list[str]], blattname: str = "Leistungen") -> bytes:
    mappe = Workbook()
    blatt = mappe.active
    blatt.title = blattname
    blatt.append(EXPORT_KOPF)
    for zeile in zeilen:
        werte = list(zeile)
        # Preis, Anzahl und USt als echte Zahlen, damit Excel damit rechnen kann
        for i in (6, 7, 8):
            try:
                werte[i] = float(zahlen.parsen(werte[i]))
            except zahlen.ZahlFehler:
                pass
        blatt.append(werte)
    kopf_stil = PatternFill("solid", fgColor="E3F0EC")
    for zelle in blatt[1]:
        zelle.font = Font(bold=True)
        zelle.fill = kopf_stil
    for zeile in blatt.iter_rows(min_row=2, min_col=7, max_col=7):
        zeile[0].number_format = "#,##0.00"
    for i, breite in enumerate((10, 32, 40, 14, 13, 11, 12, 9, 7), start=1):
        blatt.column_dimensions[get_column_letter(i)].width = breite
    blatt.freeze_panes = "A2"
    puffer = io.BytesIO()
    mappe.save(puffer)
    return puffer.getvalue()


def export(leistungen, format_: str) -> bytes:
    zeilen = _export_zeilen(leistungen)
    return xlsx_erzeugen(zeilen) if format_ == "xlsx" else csv_erzeugen(zeilen)


def vorlage(format_: str) -> bytes:
    return xlsx_erzeugen(VORLAGE_ZEILEN, "Vorlage") if format_ == "xlsx" else csv_erzeugen(VORLAGE_ZEILEN)
