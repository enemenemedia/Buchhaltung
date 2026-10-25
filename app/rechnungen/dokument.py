"""PDF der Rechnung (Sichtbeleg zur XRechnung) mit den Pflichtangaben nach § 14 UStG –
auch für Stornorechnungen und Rechnungskorrekturen (§ 31 Abs. 5 UStDV)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from .. import zahlen
from ..angebote.dokument import Belegdokument
from ..stammdaten import service as stammdaten
from . import xrechnung

BELEGART = {"rechnung": "Rechnung", "korrektur": "Rechnungskorrektur", "storno": "Stornorechnung"}


def _d(iso: str | None) -> str:
    return date.fromisoformat(iso).strftime("%d.%m.%Y") if iso else ""


class Rechnungsdokument(Belegdokument):
    belegart = "Rechnung"

    def __init__(self, kopf: dict, positionen, verkaeufer, datenverzeichnis: Path):
        daten = {**kopf, "preisausweis": "netto", "nummer": kopf.get("nummer") or "(Entwurf)"}
        self.art = kopf.get("art") or "rechnung"
        self.belegart = BELEGART[self.art]
        super().__init__(daten, positionen, verkaeufer, datenverzeichnis)

    @property
    def bezug(self) -> dict | None:
        if self.art == "rechnung":
            return None
        return {"nummer": self.a.get("bezug_nummer"), "datum": self.a.get("bezug_datum"),
                "bezahlt": bool(self.a.get("bezug_bezahlt"))}

    def vorausbezahlt(self):
        return zahlen.parsen(self.a.get("vorausbezahlt") or "0") if self.art == "korrektur" else zahlen.parsen("0")

    def belegdatum(self) -> str:
        return _d(self.a["rechnungsdatum"])

    def leistung(self) -> tuple[str, str]:
        a = self.a
        if a.get("leistung_bis"):
            return "Leistungszeitraum", f"{_d(a['leistung_von'])} – {_d(a['leistung_bis'])}"
        return "Leistungsdatum", _d(a["leistung_von"])

    def info_zeilen(self) -> list[tuple[str, str]]:
        a = self.a
        nr_label = {"rechnung": "Rechnungs-Nr.", "korrektur": "Korrektur-Nr.", "storno": "Storno-Nr."}[self.art]
        zeilen = [(nr_label, a["nummer"]), ("Datum", self.belegdatum())]
        if self.bezug:
            zeilen.append(("zu Rechnung", f"{self.bezug['nummer']} vom {_d(self.bezug['datum'])}"))
        zeilen += [self.leistung(), ("Kunden-Nr.", a["kundennummer"])]
        if a.get("bestellnummer"):
            zeilen.append(("Ihre Bestellung", a["bestellnummer"]))
        if a.get("kaeufer_referenz") and a["kaeufer_referenz"] != a["kundennummer"]:
            zeilen.append(("Ihre Referenz", a["kaeufer_referenz"]))
        if self.art != "storno":
            zeilen.append(("Fällig am", _d(a["faellig_am"])))
        return zeilen

    def summen_zeilen(self) -> list[tuple[str, str, bool]]:
        zeilen = super().summen_zeilen()
        vorher = self.vorausbezahlt()
        if vorher:
            rest = self.summen.gesamt - vorher
            zeilen.append(("abzüglich bereits gezahlt", zahlen.geld(-vorher), False))
            zeilen.append(("Noch zu zahlen" if rest >= 0 else "Erstattung an Sie", zahlen.geld(abs(rest)), True))
        return zeilen

    def hinweise(self) -> list[str]:
        a, e = self.a, self.e
        texte = []
        if self.art == "storno":
            texte.append(f"Hiermit stornieren wir die Rechnung {self.bezug['nummer']} vom {_d(self.bezug['datum'])} "
                         "in voller Höhe.")
        elif self.art == "korrektur":
            texte.append(f"Diese Rechnungskorrektur ersetzt die Rechnung {self.bezug['nummer']} vom "
                         f"{_d(self.bezug['datum'])} vollständig.")
        if self.art != "rechnung" and a.get("korrektur_grund"):
            texte.append(f"Grund: {a['korrektur_grund']}")

        if self.klein:
            texte.append("Gemäß § 19 UStG wird keine Umsatzsteuer berechnet (Kleinunternehmerregelung).")
        elif any(z.ust_satz == 0 for z in self.summen.zeilen):
            if a.get("befreiung_art") == "AE":
                texte.append("Steuerschuldnerschaft des Leistungsempfängers (Reverse Charge, § 13b UStG). "
                             f"USt-IdNr. des Leistungsempfängers: {a.get('kaeufer_ust_id') or '–'}.")
            else:
                texte.append(f"Positionen mit 0 % USt: {a.get('befreiung_grund') or xrechnung.GRUND_STEUERFREI}.")

        if self.art == "storno":
            texte.append(xrechnung.zahlungsbedingung(a, "storno", self.bezug))
            return texte
        vorher = self.vorausbezahlt()
        rest = self.summen.gesamt - vorher
        konto = f"IBAN {stammdaten.iban_formatiert(e['iban'])}" if e["iban"] else ""
        if e["bic"]:
            konto += f", BIC {e['bic']}"
        if e["bank"]:
            konto += f" ({e['bank']})"
        if vorher and rest < 0:
            texte.append(f"Den zu viel gezahlten Betrag von {zahlen.geld(-rest)} erstatten wir Ihnen.")
        elif vorher and rest == 0:
            texte.append("Der Betrag ist durch Ihre bereits geleistete Zahlung vollständig beglichen.")
        else:
            betrag = rest if vorher else self.summen.gesamt
            bedingung = xrechnung.zahlungsbedingung({**a, "vorausbezahlt": "0"}) if vorher else xrechnung.zahlungsbedingung(a)
            texte.append(f"{bedingung} Bitte überweisen Sie {zahlen.geld(betrag)} unter Angabe der Nummer "
                         f"{a['nummer']}" + (f" auf das Konto {konto}." if konto else "."))
        return texte

    def schluss(self) -> str:
        if self.art == "storno":
            return "Bei Fragen zu dieser Stornierung stehen wir Ihnen gern zur Verfügung."
        return "Vielen Dank für Ihren Auftrag und die gute Zusammenarbeit."


def erzeugen(kopf: dict, positionen, verkaeufer, datenverzeichnis: Path) -> bytes:
    return Rechnungsdokument(kopf, positionen, verkaeufer, datenverzeichnis).erzeugen()
