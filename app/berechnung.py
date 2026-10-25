"""Summenberechnung für Angebote (und später Rechnungen).

Bei Nettopreisen wird netto-basiert gerechnet, wie es auch die XRechnung verlangt:
  Positionsbetrag   = Menge × Einzelpreis, auf Cent gerundet        (BT-131)
  Nettosumme        = Summe der Positionsbeträge                    (BT-106)
  USt je Steuersatz = Summe der Positionsbeträge × Satz, gerundet   (BT-117)
  Gesamtbetrag      = Nettosumme + USt                              (BT-112)

Preisausweis:
  'netto'  – Einzel- und Positionspreise netto, darunter USt und Gesamtbetrag
             (netto-basiert wie oben)
  'brutto' – Einzel- und Positionspreise inkl. USt. Damit die ausgewiesenen
             Positionen exakt den Gesamtbetrag ergeben, wird hier brutto-basiert
             gerechnet: Gesamtbetrag = Summe der Bruttopositionen, die enthaltene
             USt je Satz = Brutto − Brutto / (1 + Satz).
Bei Kleinunternehmern (§ 19 UStG) wird keine USt berechnet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from . import zahlen

VIERTEL_CENT = Decimal("0.0001")


@dataclass
class Zeile:
    menge: Decimal
    einzelpreis_netto: Decimal
    ust_satz: Decimal
    betrag_netto: Decimal
    einzelpreis_brutto: Decimal
    betrag_brutto: Decimal

    def einzelpreis(self, ausweis: str) -> Decimal:
        return self.einzelpreis_brutto if ausweis == "brutto" else self.einzelpreis_netto

    def betrag(self, ausweis: str) -> Decimal:
        return self.betrag_brutto if ausweis == "brutto" else self.betrag_netto


@dataclass
class Steuergruppe:
    satz: Decimal
    basis: Decimal
    betrag: Decimal


@dataclass
class Summen:
    zeilen: list[Zeile]
    netto: Decimal
    ust: Decimal
    gesamt: Decimal
    steuergruppen: list[Steuergruppe] = field(default_factory=list)
    kleinunternehmer: bool = False


def berechnen(positionen, kleinunternehmer: bool = False, ausweis: str = "netto") -> Summen:
    """positionen: Folge von Objekten mit menge, einzelpreis, ust_satz (Text oder Decimal)."""
    zeilen: list[Zeile] = []
    gruppen: dict[Decimal, Decimal] = {}
    for p in positionen:
        menge = zahlen.parsen(p["menge"])
        preis = zahlen.parsen(p["einzelpreis"])
        satz = Decimal(0) if kleinunternehmer else zahlen.parsen(p["ust_satz"])
        netto = zahlen.runden(menge * preis)
        faktor = 1 + satz / 100
        zeilen.append(Zeile(
            menge=menge, einzelpreis_netto=preis, ust_satz=satz, betrag_netto=netto,
            einzelpreis_brutto=zahlen.runden(preis * faktor, VIERTEL_CENT),
            betrag_brutto=zahlen.runden(netto * faktor),
        ))
        gruppen[satz] = gruppen.get(satz, Decimal(0)) + netto

    if ausweis == "brutto" and not kleinunternehmer:
        brutto_gruppen: dict[Decimal, Decimal] = {}
        for z in zeilen:
            brutto_gruppen[z.ust_satz] = brutto_gruppen.get(z.ust_satz, Decimal(0)) + z.betrag_brutto
        steuergruppen = []
        for satz, brutto in sorted(brutto_gruppen.items(), key=lambda g: -g[0]):
            basis = zahlen.runden(brutto / (1 + satz / 100))
            steuergruppen.append(Steuergruppe(satz, basis, brutto - basis))
        gesamt = sum(brutto_gruppen.values(), Decimal(0))
        netto = sum((g.basis for g in steuergruppen), Decimal(0))
        return Summen(zeilen=zeilen, netto=netto, ust=gesamt - netto, gesamt=gesamt,
                      steuergruppen=steuergruppen, kleinunternehmer=False)

    steuergruppen = [
        Steuergruppe(satz, basis, zahlen.runden(basis * satz / 100))
        for satz, basis in sorted(gruppen.items(), key=lambda g: -g[0])
    ]
    netto = sum((z.betrag_netto for z in zeilen), Decimal(0))
    ust = sum((g.betrag for g in steuergruppen), Decimal(0))
    return Summen(zeilen=zeilen, netto=zahlen.runden(netto), ust=zahlen.runden(ust),
                  gesamt=zahlen.runden(netto + ust), steuergruppen=steuergruppen,
                  kleinunternehmer=kleinunternehmer)
