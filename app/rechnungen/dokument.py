"""PDF der Rechnung (Sichtbeleg zur XRechnung) mit den Pflichtangaben nach § 14 UStG."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from .. import zahlen
from ..angebote.dokument import Belegdokument
from ..stammdaten import service as stammdaten
from . import xrechnung


def _d(iso: str | None) -> str:
    return date.fromisoformat(iso).strftime("%d.%m.%Y") if iso else ""


class Rechnungsdokument(Belegdokument):
    belegart = "Rechnung"

    def __init__(self, kopf: dict, positionen, verkaeufer, datenverzeichnis: Path):
        daten = {**kopf, "preisausweis": "netto", "nummer": kopf.get("nummer") or "(Entwurf)"}
        super().__init__(daten, positionen, verkaeufer, datenverzeichnis)

    def belegdatum(self) -> str:
        return _d(self.a["rechnungsdatum"])

    def leistung(self) -> tuple[str, str]:
        a = self.a
        if a.get("leistung_bis"):
            return "Leistungszeitraum", f"{_d(a['leistung_von'])} – {_d(a['leistung_bis'])}"
        return "Leistungsdatum", _d(a["leistung_von"])

    def info_zeilen(self) -> list[tuple[str, str]]:
        a = self.a
        zeilen = [("Rechnungs-Nr.", a["nummer"]), ("Rechnungsdatum", self.belegdatum()), self.leistung(),
                  ("Kunden-Nr.", a["kundennummer"])]
        if a.get("bestellnummer"):
            zeilen.append(("Ihre Bestellung", a["bestellnummer"]))
        if a.get("kaeufer_referenz") and a["kaeufer_referenz"] != a["kundennummer"]:
            zeilen.append(("Ihre Referenz", a["kaeufer_referenz"]))
        zeilen.append(("Fällig am", _d(a["faellig_am"])))
        return zeilen

    def hinweise(self) -> list[str]:
        a, e = self.a, self.e
        texte = []
        if self.klein:
            texte.append("Gemäß § 19 UStG wird keine Umsatzsteuer berechnet (Kleinunternehmerregelung).")
        elif any(z.ust_satz == 0 for z in self.summen.zeilen):
            if a.get("befreiung_art") == "AE":
                texte.append("Steuerschuldnerschaft des Leistungsempfängers (Reverse Charge, § 13b UStG). "
                             f"USt-IdNr. des Leistungsempfängers: {a.get('kaeufer_ust_id') or '–'}.")
            else:
                texte.append(f"Positionen mit 0 % USt: {a.get('befreiung_grund') or xrechnung.GRUND_STEUERFREI}.")
        konto = f"IBAN {stammdaten.iban_formatiert(e['iban'])}" if e["iban"] else ""
        if e["bic"]:
            konto += f", BIC {e['bic']}"
        if e["bank"]:
            konto += f" ({e['bank']})"
        texte.append(f"{xrechnung.zahlungsbedingung(a)} Bitte überweisen Sie {zahlen.geld(self.summen.gesamt)} "
                     f"unter Angabe der Rechnungsnummer {a['nummer']}" + (f" auf das Konto {konto}." if konto else "."))
        return texte

    def schluss(self) -> str:
        return "Vielen Dank für Ihren Auftrag und die gute Zusammenarbeit."


def erzeugen(kopf: dict, positionen, verkaeufer, datenverzeichnis: Path) -> bytes:
    return Rechnungsdokument(kopf, positionen, verkaeufer, datenverzeichnis).erzeugen()
