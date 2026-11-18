"""Auswertung der Belege nach Tags – als Seite und als CSV.

- Je Tag: Anzahl der Belege und Summen (netto, USt, brutto); dazu „(ohne Tag)“.
- Ein Beleg mit mehreren Tags zählt bei jedem seiner Tags; die Gesamtsumme zählt jeden Beleg
  nur einmal.
- Auch ohne Festschreibung: Entwürfe sind standardmäßig enthalten (abschaltbar).
- Stornierte Belege und ihre Gegenbuchungen bleiben außen vor (sie heben sich ohnehin auf).
- Zeitraum: Kalenderjahr nach Belegdatum (Standard) oder nach Zahlungsdatum.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

from . import kategorien, service
from .tags import fuer_belege

OHNE_TAG = "(ohne Tag)"
DATUMSARTEN = {"beleg": "Belegdatum", "zahlung": "Zahlungsdatum"}
ARTEN = {"ausgabe": "Ausgaben", "einnahme": "Einnahmen"}


@dataclass
class Gruppe:
    tag: str
    belege: list = field(default_factory=list)
    aktiv: bool = True

    @property
    def anzahl(self) -> int:
        return len(self.belege)

    def summe(self, feld: str) -> int:
        return sum(b[feld] for b in self.belege)

    @property
    def netto(self) -> int:
        return self.summe("netto_cent")

    @property
    def ust(self) -> int:
        return self.summe("ust_cent")

    @property
    def brutto(self) -> int:
        return self.summe("brutto_cent")


@dataclass
class Auswertung:
    jahr: int
    art: str
    datum: str
    nur_festgeschrieben: bool
    gruppen: list[Gruppe]
    gesamt: Gruppe
    tags_je_beleg: dict[int, list[str]]
    entwuerfe: int
    mehrfach: int

    @property
    def beschreibung(self) -> str:
        return (f"{ARTEN[self.art]} {self.jahr} nach {DATUMSARTEN[self.datum]}, "
                + ("nur festgeschriebene Belege" if self.nur_festgeschrieben else "einschließlich Entwürfe"))


def berechnen(con, jahr: int, *, art: str = "ausgabe", datum: str = "beleg",
              nur_festgeschrieben: bool = False) -> Auswertung:
    art = art if art in ARTEN else "ausgabe"
    datum = datum if datum in DATUMSARTEN else "beleg"
    spalte = "belegdatum" if datum == "beleg" else "zahlungsdatum"
    status = "('festgeschrieben')" if nur_festgeschrieben else "('festgeschrieben', 'entwurf')"
    belege = con.execute(
        f"""SELECT * FROM belege WHERE art = ? AND status IN {status} AND quelle <> 'storno'
            AND substr({spalte}, 1, 4) = ? ORDER BY {spalte}, id""", (art, str(jahr))).fetchall()
    zuordnung = fuer_belege(con, [b["id"] for b in belege])

    gruppen: dict[str, Gruppe] = {}
    for t in con.execute("SELECT name, aktiv FROM tags ORDER BY reihenfolge, name"):
        gruppen[t["name"]] = Gruppe(t["name"], aktiv=bool(t["aktiv"]))
    ohne = Gruppe(OHNE_TAG)
    for b in belege:
        namen = zuordnung[b["id"]]
        for n in namen:
            gruppen.setdefault(n, Gruppe(n)).belege.append(b)
        if not namen:
            ohne.belege.append(b)
    liste = [g for g in gruppen.values() if g.belege or g.aktiv]
    if ohne.belege:
        liste.append(ohne)
    return Auswertung(jahr=jahr, art=art, datum=datum, nur_festgeschrieben=nur_festgeschrieben, gruppen=liste,
                      gesamt=Gruppe("Gesamt", list(belege)), tags_je_beleg=zuordnung,
                      entwuerfe=sum(1 for b in belege if b["status"] == "entwurf"),
                      mehrfach=sum(1 for n in zuordnung.values() if len(n) > 1))


def _betrag(c: int) -> str:
    return service.als_eingabe(c)


def csv_export(a: Auswertung) -> str:
    """Belege gruppiert nach Tag mit Zwischensummen, danach die Übersicht der Summen je Tag."""
    puffer = io.StringIO()
    w = csv.writer(puffer, delimiter=";", lineterminator="\r\n")
    w.writerow([f"Belege nach Tags – {a.beschreibung}"])
    w.writerow([])
    w.writerow(["Tag", "Belegnummer", "Status", "Belegdatum", "Bezahlt am", "Partner", "Rechnungsnummer",
                "Beschreibung", "Kategorie", "Alle Tags", "Netto", "USt", "Brutto"])
    for g in a.gruppen:
        if not g.belege:
            continue
        for b in g.belege:
            w.writerow([g.tag, b["belegnummer"] or f"Entwurf #{b['id']}", service.STATUS[b["status"]],
                        b["belegdatum"], b["zahlungsdatum"] or "", b["partner"], b["rechnungsnummer"] or "",
                        b["beschreibung"], kategorien.bezeichnung(b["kategorie"]),
                        ", ".join(a.tags_je_beleg[b["id"]]), _betrag(b["netto_cent"]), _betrag(b["ust_cent"]),
                        _betrag(b["brutto_cent"])])
        w.writerow([f"Summe {g.tag}", "", "", "", "", "", "", f"{g.anzahl} Beleg(e)", "", "",
                    _betrag(g.netto), _betrag(g.ust), _betrag(g.brutto)])
    w.writerow([])
    w.writerow(["Übersicht", "Anzahl", "Netto", "USt", "Brutto"])
    for g in a.gruppen:
        w.writerow([g.tag, g.anzahl, _betrag(g.netto), _betrag(g.ust), _betrag(g.brutto)])
    w.writerow(["Gesamt (jeder Beleg einmal)", a.gesamt.anzahl, _betrag(a.gesamt.netto), _betrag(a.gesamt.ust),
                _betrag(a.gesamt.brutto)])
    if a.mehrfach:
        w.writerow([])
        w.writerow([f"Hinweis: {a.mehrfach} Beleg(e) haben mehrere Tags und sind in jeder ihrer Tag-Summen enthalten."])
    if a.entwuerfe:
        w.writerow([f"Hinweis: {a.entwuerfe} Beleg(e) sind noch nicht festgeschrieben (Entwurf)."])
    return "﻿" + puffer.getvalue()
