"""EÜR als PDF (A4) – Übersicht, Anlagenverzeichnis und Monatswerte."""

from __future__ import annotations

import io
from datetime import datetime
from decimal import Decimal
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .. import PROGRAMM_NAME, zahlen
from .service import Ergebnis

GRUEN = colors.HexColor("#1f6f5c")
HELL = colors.HexColor("#e3f0ec")
LINIE = colors.HexColor("#d5dcd9")

S_TITEL = ParagraphStyle("titel", fontName="Helvetica-Bold", fontSize=16, leading=20, spaceAfter=2)
S_UNTER = ParagraphStyle("unter", fontName="Helvetica", fontSize=9, leading=12, textColor=colors.HexColor("#5b6762"))
S_H2 = ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=11, leading=14, spaceBefore=10, spaceAfter=4,
                      textColor=GRUEN)
S_TEXT = ParagraphStyle("text", fontName="Helvetica", fontSize=8.5, leading=11)
S_KLEIN = ParagraphStyle("klein", fontName="Helvetica", fontSize=7.5, leading=10, textColor=colors.HexColor("#5b6762"))


def _e(c: int) -> str:
    return zahlen.geld(Decimal(c) / 100)


def _tabelle(daten, spalten, summenzeilen=(), kopf=True):
    t = Table(daten, colWidths=spalten, repeatRows=1 if kopf else 0)
    stil = [("FONT", (0, 0), (-1, -1), "Helvetica", 8.5), ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINIE), ("ALIGN", (-1, 0), (-1, -1), "RIGHT"),
            ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
    if kopf:
        stil += [("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8.5), ("BACKGROUND", (0, 0), (-1, 0), HELL)]
    for z in summenzeilen:
        stil += [("FONT", (0, z), (-1, z), "Helvetica-Bold", 8.5), ("LINEABOVE", (0, z), (-1, z), 0.8, colors.black)]
    t.setStyle(TableStyle(stil))
    return t


def erzeugen(erg: Ergebnis, firma: str) -> bytes:
    puffer = io.BytesIO()
    doc = SimpleDocTemplate(puffer, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm,
                            bottomMargin=16 * mm, title=f"EÜR {erg.jahr}", author=firma or "", creator=PROGRAMM_NAME)
    breite = A4[0] - 36 * mm
    teile = [Paragraph(f"Einnahmen-Überschuss-Rechnung {erg.jahr}", S_TITEL),
             Paragraph(escape(firma or "") + (" · Kleinunternehmer (§ 19 UStG)" if erg.kleinunternehmer else "")
                       + f" · Wirtschaftsjahr 01.01.–31.12.{erg.jahr}", S_UNTER), Spacer(1, 6)]

    zeilen = [["Betriebseinnahmen", "Betrag"]]
    zeilen += [[Paragraph(escape(z.text), S_TEXT), _e(z.cent)] for z in erg.einnahmen] or [["(keine)", _e(0)]]
    zeilen.append(["Summe Betriebseinnahmen", _e(erg.summe_einnahmen)])
    teile.append(_tabelle(zeilen, [breite - 35 * mm, 35 * mm], [len(zeilen) - 1]))
    teile.append(Spacer(1, 8))

    zeilen = [["Betriebsausgaben", "Betrag"]]
    for z in erg.ausgaben:
        text = escape(z.text) + (f"<br/><font size=7 color='#5b6762'>{escape(z.hinweis)}</font>" if z.hinweis else "")
        zeilen.append([Paragraph(text, S_TEXT), _e(z.cent)])
    if len(zeilen) == 1:
        zeilen.append(["(keine)", _e(0)])
    zeilen.append(["Summe Betriebsausgaben", _e(erg.summe_ausgaben)])
    teile.append(_tabelle(zeilen, [breite - 35 * mm, 35 * mm], [len(zeilen) - 1]))
    teile.append(Spacer(1, 8))
    teile.append(_tabelle([["Gewinn" if erg.gewinn >= 0 else "Verlust", _e(erg.gewinn)]],
                          [breite - 35 * mm, 35 * mm], [0], kopf=False))

    if erg.anlagen:
        teile.append(Paragraph("Anlagenverzeichnis", S_H2))
        zeilen = [["Beleg", "Bezeichnung", "Anschaffung", "AK", "ND", "AfA bisher", f"AfA {erg.jahr}", "Restwert"]]
        for a in erg.anlagen:
            zeilen.append([a.belegnummer, Paragraph(escape(a.beschreibung), S_TEXT),
                           f"{a.anschaffung[8:10]}.{a.anschaffung[5:7]}.{a.anschaffung[:4]}", _e(a.ak_cent),
                           f"{a.nutzungsdauer} J.", _e(a.afa_bisher), _e(a.afa_jahr), _e(a.restwert)])
        t = _tabelle(zeilen, [22 * mm, breite - 142 * mm, 20 * mm, 22 * mm, 10 * mm, 22 * mm, 22 * mm, 24 * mm])
        t.setStyle(TableStyle([("ALIGN", (3, 0), (-1, -1), "RIGHT")]))
        teile.append(t)

    teile.append(Paragraph("Monatswerte (Zahlungen, brutto)", S_H2))
    zeilen = [["Monat", "Einnahmen", "Ausgaben", "USt", "Vorsteuer", "Zahllast"]]
    for m in erg.monate:
        zeilen.append([m["monat"], _e(m["einnahmen"]), _e(m["ausgaben"]), _e(m["ust"]), _e(m["vorsteuer"]),
                       _e(m["ust"] - m["vorsteuer"])])
    t = _tabelle(zeilen, [30 * mm] + [(breite - 30 * mm) / 5] * 5)
    t.setStyle(TableStyle([("ALIGN", (1, 0), (-1, -1), "RIGHT")]))
    teile.append(t)

    teile.append(Spacer(1, 8))
    for h in erg.hinweise:
        teile.append(Paragraph("Hinweis: " + escape(h), S_KLEIN))
    teile.append(Paragraph(f"Erstellt am {datetime.now():%d.%m.%Y %H:%M} mit {PROGRAMM_NAME}. "
                           "Die Zuordnung zu den Zeilen der Anlage EÜR bitte mit der Steuerberatung abstimmen.", S_KLEIN))
    doc.build(teile)
    return puffer.getvalue()
