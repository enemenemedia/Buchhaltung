"""Dezimalzahlen für Preise und Mengen.

Alle Beträge werden mit Decimal gerechnet und als Text in der Datenbank
abgelegt ('1234.5'), damit keine Rundungsfehler entstehen.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")


class ZahlFehler(ValueError):
    pass


def parsen(wert, *, feld: str = "Zahl") -> Decimal:
    """Liest eine Zahl in deutscher oder englischer Schreibweise.

    Erlaubt: 85 | 85,5 | 85.50 | 1.234,56 | 1,234.56 | 1 234,56 | '85,00 €' | 1.5
    Ein einzelner Punkt gilt als Dezimaltrenner (1.5 = eineinhalb), mehrere
    Punkte als Tausendertrennzeichen (1.234.567).
    """
    if isinstance(wert, Decimal):
        return wert
    if isinstance(wert, (int, float)) and not isinstance(wert, bool):
        return Decimal(str(wert))
    text = str(wert or "").strip()
    text = re.sub(r"[€\s  ]|EUR", "", text, flags=re.I)
    if not text:
        raise ZahlFehler(f"{feld} fehlt.")
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):     # 1.234,56
            text = text.replace(".", "").replace(",", ".")
        else:                                       # 1,234.56
            text = text.replace(",", "")
    elif "," in text:
        if text.count(",") > 1:
            raise ZahlFehler(f"{feld}: „{wert}“ ist keine gültige Zahl.")
        text = text.replace(",", ".")
    elif text.count(".") > 1:
        text = text.replace(".", "")
    if not re.fullmatch(r"[+-]?\d+(\.\d+)?", text):
        raise ZahlFehler(f"{feld}: „{wert}“ ist keine gültige Zahl.")
    try:
        return Decimal(text)
    except InvalidOperation:
        raise ZahlFehler(f"{feld}: „{wert}“ ist keine gültige Zahl.") from None


def speicherform(zahl: Decimal) -> str:
    """Kanonische Textform für die Datenbank (ohne überflüssige Nullen)."""
    text = format(zahl.normalize(), "f")
    return "0" if text in ("-0", "") else text


def runden(zahl: Decimal, stellen: Decimal = CENT) -> Decimal:
    return zahl.quantize(stellen, rounding=ROUND_HALF_UP)


def _deutsch(zahl: Decimal, mindest: int, hoechst: int) -> str:
    stellen = max(mindest, min(hoechst, -zahl.normalize().as_tuple().exponent if zahl % 1 else 0))
    gerundet = zahl.quantize(Decimal(1).scaleb(-stellen), rounding=ROUND_HALF_UP)
    text = f"{gerundet:,.{stellen}f}"
    return text.replace(",", "\u0001").replace(".", ",").replace("\u0001", ".")


def geld(wert, mit_waehrung: bool = True) -> str:
    """1234.5 -> '1.234,50 €' (mind. 2, höchstens 4 Nachkommastellen)."""
    if wert in (None, ""):
        return ""
    text = _deutsch(parsen(wert), 2, 4)
    return f"{text} €" if mit_waehrung else text


def menge(wert) -> str:
    """1.5 -> '1,5'; 2 -> '2' (höchstens 3 Nachkommastellen)."""
    if wert in (None, ""):
        return ""
    return _deutsch(parsen(wert), 0, 3)
