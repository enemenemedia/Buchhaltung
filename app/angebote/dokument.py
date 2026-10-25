"""PDF-Erzeugung für Angebote – und Basis für Rechnungen (reportlab).

Layout angelehnt an DIN 5008 (Form B): Anschriftfeld für Fensterumschläge,
Informationsblock rechts, Logo oben rechts, Fußzeile mit Firmen-, Bank- und
Steuerangaben auf jeder Seite.
"""

from __future__ import annotations

import io
from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import (BaseDocTemplate, Frame, KeepTogether, NextPageTemplate, PageTemplate,
                                Paragraph, Spacer, Table, TableStyle)

from .. import berechnung, zahlen
from ..leistungen import service as leistungen
from ..stammdaten import service as stammdaten

BREITE, HOEHE = A4
RAND_LINKS, RAND_RECHTS = 25 * mm, 20 * mm
INHALT_BREITE = BREITE - RAND_LINKS - RAND_RECHTS
FUSS_HOEHE = 30 * mm

DUNKEL = colors.HexColor("#1d2420")
GRAU = colors.HexColor("#5f6b64")
LINIE = colors.HexColor("#c9d0cc")
AKZENT = colors.HexColor("#1f6f5c")
HINTERGRUND = colors.HexColor("#f1f4f2")

S_NORMAL = ParagraphStyle("normal", fontName="Helvetica", fontSize=9.5, leading=13, textColor=DUNKEL)
S_KLEIN = ParagraphStyle("klein", parent=S_NORMAL, fontSize=8, leading=10.5, textColor=GRAU)
S_TITEL = ParagraphStyle("titel", parent=S_NORMAL, fontName="Helvetica-Bold", fontSize=15, leading=19)
S_BETREFF = ParagraphStyle("betreff", parent=S_NORMAL, fontName="Helvetica-Bold", fontSize=10.5, leading=14)
S_POS_TITEL = ParagraphStyle("postitel", parent=S_NORMAL, fontName="Helvetica-Bold")
S_RECHTS = ParagraphStyle("rechts", parent=S_NORMAL, alignment=TA_RIGHT)
S_KOPF = ParagraphStyle("kopf", parent=S_NORMAL, fontName="Helvetica-Bold", fontSize=8, leading=10, textColor=GRAU)
S_KOPF_RECHTS = ParagraphStyle("kopfrechts", parent=S_KOPF, alignment=TA_RIGHT)
S_ABSCHNITT = ParagraphStyle("abschnitt", parent=S_NORMAL, fontName="Helvetica-Bold", spaceBefore=4, spaceAfter=2)


def _text(wert: str) -> str:
    """Text für Paragraph: maskieren und Zeilenumbrüche erhalten."""
    return escape(wert or "").replace("\n", "<br/>")


def _datum(iso: str) -> str:
    return date.fromisoformat(iso).strftime("%d.%m.%Y")


def _geld(wert) -> str:
    return zahlen.geld(wert)


class _NummerierteSeiten(rl_canvas.Canvas):
    """Canvas mit „Seite x von y“ (zweiter Durchlauf für die Gesamtseitenzahl)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._seiten = []

    def showPage(self):
        self._seiten.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        gesamt = len(self._seiten)
        for zustand in self._seiten:
            self.__dict__.update(zustand)
            self.setFont("Helvetica", 7.5)
            self.setFillColor(GRAU)
            self.drawRightString(BREITE - RAND_RECHTS, 10 * mm, f"Seite {self._pageNumber} von {gesamt}")
            super().showPage()
        super().save()


class Belegdokument:
    """Gemeinsames Layout für Angebote und Rechnungen. Unterklassen passen über
    belegart, info_zeilen(), hinweise() und schluss() den Inhalt an."""

    belegart = "Angebot"

    def __init__(self, angebot, positionen, einstellungen, datenverzeichnis: Path):
        self.a = angebot
        self.positionen = positionen
        self.e = einstellungen
        self.logo = stammdaten.logo_pfad(datenverzeichnis)
        self.ausweis = angebot["preisausweis"]
        self.klein = bool(angebot["kleinunternehmer"])
        self.summen = berechnung.berechnen(positionen, self.klein, self.ausweis)

    # -- Seitenelemente ----------------------------------------------------

    def _logo(self, c, max_b: float, max_h: float, oben: float) -> None:
        if not self.logo.is_file():
            return
        bild = ImageReader(str(self.logo))
        b, h = bild.getSize()
        faktor = min(max_b / b, max_h / h)
        c.drawImage(bild, BREITE - RAND_RECHTS - b * faktor, oben - h * faktor, b * faktor, h * faktor,
                    mask="auto")

    def _fusszeile(self, c) -> None:
        e = self.e
        c.setStrokeColor(LINIE)
        c.setLineWidth(0.5)
        c.line(RAND_LINKS, FUSS_HOEHE + 2 * mm, BREITE - RAND_RECHTS, FUSS_HOEHE + 2 * mm)
        spalten = [
            [e["firma"], e["strasse"], f"{e['plz']} {e['ort']}".strip(),
             f"Inhaber/GF: {e['inhaber']}" if e["inhaber"] else ""],
            [f"Tel. {e['telefon']}" if e["telefon"] else "", e["email"], e["website"]],
            [e["bank"], f"IBAN {stammdaten.iban_formatiert(e['iban'])}" if e["iban"] else "",
             f"BIC {e['bic']}" if e["bic"] else ""],
            [f"USt-IdNr. {e['ust_id']}" if e["ust_id"] else "",
             f"St.-Nr. {e['steuernummer']}" if e["steuernummer"] else "",
             " ".join(x for x in (e["registergericht"], e["registernummer"]) if x)],
        ]
        # Spaltenbreiten passend zum Inhalt (IBAN ist die längste Angabe)
        breiten = [38 * mm, 40 * mm, 50 * mm, INHALT_BREITE - 128 * mm]
        c.setFont("Helvetica", 7)
        c.setFillColor(GRAU)
        x = RAND_LINKS
        for breite, zeilen in zip(breiten, spalten):
            y = FUSS_HOEHE - 2 * mm
            for z in (z for z in zeilen if z):
                # Text auf Spaltenbreite kürzen
                while c.stringWidth(z, "Helvetica", 7) > breite - 2 * mm and len(z) > 4:
                    z = z[:-2] + "…"
                c.drawString(x, y, z)
                y -= 3.2 * mm
            x += breite

    def _entwurf_wasserzeichen(self, c) -> None:
        if self.a["status"] != "entwurf":
            return
        c.saveState()
        c.setFont("Helvetica-Bold", 90)
        c.setFillColor(colors.Color(0.85, 0.2, 0.15, alpha=0.08))
        c.translate(BREITE / 2, HOEHE / 2)
        c.rotate(35)
        c.drawCentredString(0, 0, "ENTWURF")
        c.restoreState()

    def erste_seite(self, c, _doc) -> None:
        a, e = self.a, self.e
        c.saveState()
        self._entwurf_wasserzeichen(c)
        self._logo(c, 55 * mm, 22 * mm, HOEHE - 12 * mm)

        # Rücksendeangabe und Anschrift (Anschriftfeld DIN 5008 Form B: 45 mm von oben)
        absender = " · ".join(x for x in (e["firma"], e["strasse"], f"{e['plz']} {e['ort']}".strip()) if x)
        c.setFont("Helvetica", 7)
        c.setFillColor(GRAU)
        c.drawString(RAND_LINKS, HOEHE - 50 * mm, absender[:95])
        c.setStrokeColor(LINIE)
        c.line(RAND_LINKS, HOEHE - 51 * mm, RAND_LINKS + 85 * mm, HOEHE - 51 * mm)
        c.setFont("Helvetica", 10)
        c.setFillColor(DUNKEL)
        y = HOEHE - 56 * mm
        for zeile in a["empfaenger"].splitlines()[:6]:
            c.drawString(RAND_LINKS, y, zeile[:52])
            y -= 4.6 * mm

        # Informationsblock rechts
        info = self.info_zeilen()
        if e["kontakt_name"]:
            info.append(("Ihr Kontakt", e["kontakt_name"]))
        if e["kontakt_telefon"] or e["telefon"]:
            info.append(("Telefon", e["kontakt_telefon"] or e["telefon"]))
        if e["kontakt_email"] or e["email"]:
            info.append(("E-Mail", e["kontakt_email"] or e["email"]))
        x_label, x_wert = 116 * mm, 143 * mm
        y = HOEHE - 50 * mm
        for label, wert in info:
            c.setFont("Helvetica", 8)
            c.setFillColor(GRAU)
            c.drawString(x_label, y, label)
            c.setFont("Helvetica", 9)
            c.setFillColor(DUNKEL)
            c.drawString(x_wert, y, str(wert)[:40])
            y -= 4.4 * mm

        self._fusszeile(c)
        c.restoreState()

    def folgeseite(self, c, _doc) -> None:
        c.saveState()
        self._entwurf_wasserzeichen(c)
        c.setFont("Helvetica", 8)
        c.setFillColor(GRAU)
        c.drawString(RAND_LINKS, HOEHE - 15 * mm, f"{self.belegart} {self.a['nummer']} vom {self.belegdatum()}")
        self._logo(c, 30 * mm, 10 * mm, HOEHE - 10 * mm)
        self._fusszeile(c)
        c.restoreState()

    # -- Inhalt --------------------------------------------------------------

    def _positionstabelle(self) -> Table:
        mit_ust = not self.klein
        netto = self.ausweis == "netto" and not self.klein
        kopf = ["Pos.", "Leistung", "Menge",
                "Einzelpreis netto" if netto else "Einzelpreis",
                *(["USt"] if mit_ust else []),
                "Gesamt netto" if netto else "Gesamt"]
        breiten = [11 * mm, None, 24 * mm, 26 * mm, *([12 * mm] if mit_ust else []), 26 * mm]
        rest = INHALT_BREITE - sum(b for b in breiten if b)
        breiten = [b or rest for b in breiten]

        daten = [[Paragraph(k, S_KOPF_RECHTS if i >= 2 else S_KOPF) for i, k in enumerate(kopf)]]
        for nr, (p, z) in enumerate(zip(self.positionen, self.summen.zeilen), start=1):
            text = f"{_text(p['bezeichnung'])}"
            zelle = [Paragraph(text, S_POS_TITEL)]
            if p["beschreibung"]:
                zelle.append(Paragraph(_text(p["beschreibung"]), S_KLEIN))
            if p["leistungsnummer"]:
                zelle.append(Paragraph(f"Nr. {escape(p['leistungsnummer'])}", S_KLEIN))
            einheit = leistungen.einheit_name(p["einheit_code"], p["menge"])
            daten.append([
                Paragraph(str(nr), S_NORMAL), zelle,
                Paragraph(f"{zahlen.menge(p['menge'])} {escape(einheit)}", S_RECHTS),
                Paragraph(_geld(z.einzelpreis(self.ausweis)), S_RECHTS),
                *([Paragraph(f"{zahlen.menge(z.ust_satz)} %", S_RECHTS)] if mit_ust else []),
                Paragraph(_geld(z.betrag(self.ausweis)), S_RECHTS),
            ])
        t = Table(daten, colWidths=breiten, repeatRows=1)
        t.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LINEBELOW", (0, 0), (-1, 0), 0.8, DUNKEL),
            ("LINEBELOW", (0, 1), (-1, -1), 0.4, LINIE),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ]))
        return t

    def summen_zeilen(self) -> list[tuple[str, str, bool]]:
        """(Bezeichnung, Betrag, hervorgehoben) – in Unterklassen erweiterbar."""
        s = self.summen
        zeilen: list[tuple[str, str, bool]] = []
        if self.klein:
            zeilen.append(("Gesamtbetrag", _geld(s.gesamt), True))
        elif self.ausweis == "netto":
            zeilen.append(("Summe netto", _geld(s.netto), False))
            for g in s.steuergruppen:
                zeilen.append((f"zzgl. {zahlen.menge(g.satz)} % USt auf {_geld(g.basis)}", _geld(g.betrag), False))
            zeilen.append(("Gesamtbetrag", _geld(s.gesamt), True))
        else:
            zeilen.append(("Gesamtbetrag", _geld(s.gesamt), True))
            zeilen.append(("Nettobetrag", _geld(s.netto), False))
            for g in s.steuergruppen:
                if g.satz:
                    zeilen.append((f"enthaltene USt {zahlen.menge(g.satz)} %", _geld(g.betrag), False))
        return zeilen

    def _summentabelle(self) -> Table:
        zeilen = self.summen_zeilen()
        daten = [[Paragraph(f"<b>{escape(l)}</b>" if fett else escape(l), S_RECHTS if not fett else
                            ParagraphStyle("sfett", parent=S_RECHTS, fontSize=11, leading=14)),
                  Paragraph(f"<b>{w}</b>" if fett else w,
                            ParagraphStyle("sw", parent=S_RECHTS, fontSize=11 if fett else 9.5,
                                           leading=14 if fett else 13))]
                 for l, w, fett in zeilen]
        t = Table(daten, colWidths=[INHALT_BREITE - 34 * mm, 34 * mm], hAlign="RIGHT")
        stil = [("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3)]
        for i, (_, _, fett) in enumerate(zeilen):
            if fett:
                stil += [("LINEABOVE", (1, i), (1, i), 0.8, DUNKEL), ("BACKGROUND", (0, i), (-1, i), HINTERGRUND),
                         ("TOPPADDING", (0, i), (-1, i), 5), ("BOTTOMPADDING", (0, i), (-1, i), 5)]
        t.setStyle(TableStyle(stil))
        return t

    def inhalt(self) -> list:
        a = self.a
        teile: list = [Paragraph(f"{self.belegart} {escape(a['nummer'])}", S_TITEL)]
        if a["betreff"]:
            teile += [Spacer(0, 1.5 * mm), Paragraph(_text(a["betreff"]), S_BETREFF)]
        teile += [Spacer(0, 6 * mm), NextPageTemplate("folge"),
                  self._positionstabelle(), Spacer(0, 3 * mm), KeepTogether([self._summentabelle()])]

        teile += [Spacer(0, 6 * mm), *(Paragraph(escape(h), S_NORMAL) for h in self.hinweise())]

        if a["anmerkungen"]:
            teile += [Spacer(0, 5 * mm),
                      KeepTogether([Paragraph("Anmerkungen und Hinweise", S_ABSCHNITT),
                                    Paragraph(_text(a["anmerkungen"]), S_NORMAL)])]
        teile += [Spacer(0, 8 * mm), Paragraph(escape(self.schluss()), S_NORMAL),
                  Spacer(0, 6 * mm), Paragraph("Mit freundlichen Grüßen", S_NORMAL),
                  Spacer(0, 2 * mm), Paragraph(escape(self.e["kontakt_name"] or self.e["firma"]), S_NORMAL)]
        return teile

    def erzeugen(self) -> bytes:
        puffer = io.BytesIO()
        doc = BaseDocTemplate(
            puffer, pagesize=A4, title=f"{self.belegart} {self.a['nummer']}", author=self.e["firma"],
            subject=self.a["betreff"] or f"{self.belegart} {self.a['nummer']}", creator="X-Rechnung für KMU",
        )
        unten = FUSS_HOEHE + 6 * mm
        erste = Frame(RAND_LINKS, unten, INHALT_BREITE, HOEHE - 100 * mm - unten, id="erste",
                      leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
        folge = Frame(RAND_LINKS, unten, INHALT_BREITE, HOEHE - 25 * mm - unten, id="folge",
                      leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
        doc.addPageTemplates([
            PageTemplate(id="erste", frames=[erste], onPage=self.erste_seite),
            PageTemplate(id="folge", frames=[folge], onPage=self.folgeseite),
        ])
        doc.build(self.inhalt(), canvasmaker=_NummerierteSeiten)
        return puffer.getvalue()


    # -- Anpassbare Inhalte (Angebot) ------------------------------------------

    def belegdatum(self) -> str:
        return _datum(self.a["datum"])

    def info_zeilen(self) -> list[tuple[str, str]]:
        a = self.a
        return [("Angebot-Nr.", a["nummer"]), ("Datum", _datum(a["datum"])),
                ("Gültig bis", _datum(a["gueltig_bis"])), ("Kunden-Nr.", a["kundennummer"])]

    def preis_hinweis(self) -> str:
        if self.klein:
            return "Gemäß § 19 UStG wird keine Umsatzsteuer berechnet."
        if self.ausweis == "brutto":
            return "Alle Preise verstehen sich inklusive der gesetzlichen Umsatzsteuer."
        return "Alle Preise verstehen sich zuzüglich der gesetzlichen Umsatzsteuer."

    def hinweise(self) -> list[str]:
        return [self.preis_hinweis(), f"Dieses Angebot ist gültig bis zum {_datum(self.a['gueltig_bis'])}."]

    def schluss(self) -> str:
        return "Wir freuen uns auf Ihren Auftrag. Für Rückfragen stehen wir Ihnen gern zur Verfügung."


def erzeugen(angebot, positionen, einstellungen, datenverzeichnis: Path) -> bytes:
    return Belegdokument(angebot, positionen, einstellungen, datenverzeichnis).erzeugen()
