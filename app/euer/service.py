"""Einnahmen-Überschuss-Rechnung (EÜR, § 4 Abs. 3 EStG) aus den festgeschriebenen Belegen.

Grundsätze
  - Zufluss-/Abflussprinzip (§ 11 EStG): Einnahmen und Ausgaben zählen im Jahr der Zahlung.
    Belege ohne Zahlungsdatum sind (noch) nicht enthalten.
  - Ausnahmen nach Anschaffung (Belegdatum): geringwertige Wirtschaftsgüter (Sofortabzug,
    § 6 Abs. 2 EStG) und Anlagevermögen (lineare AfA, monatsgenau, § 7 Abs. 1 EStG).
  - Umsatzsteuer: vereinnahmte USt ist Betriebseinnahme, gezahlte Vorsteuer und an das
    Finanzamt gezahlte USt sind Betriebsausgaben.
  - Kleinunternehmer (§ 19 UStG, laut Stammdaten): keine Vorsteuer – Ausgaben zählen brutto.
  - Bewirtung: 70 % der Kosten abziehbar (§ 4 Abs. 5 Nr. 2 EStG); Vorsteuer voll.
  - Auch ohne Festschreibung: Entwürfe zählen standardmäßig mit (abschaltbar über
    nur_festgeschrieben). Stornierte Belege und ihre Gegenbuchungen heben sich auf.

Die Zuordnung zu den Zeilen der Anlage EÜR des jeweiligen Jahres bitte mit der
Steuerberatung abstimmen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from ..belege import kategorien

MONATE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober",
          "November", "Dezember"]


@dataclass
class Zeile:
    schluessel: str
    text: str
    cent: int
    hinweis: str = ""


@dataclass
class Anlage:
    beleg_id: int
    belegnummer: str
    beschreibung: str
    anschaffung: str
    ak_cent: int
    nutzungsdauer: int
    afa_bisher: int
    afa_jahr: int
    restwert: int


@dataclass
class Ergebnis:
    jahr: int
    kleinunternehmer: bool
    nur_festgeschrieben: bool = False
    einnahmen: list[Zeile] = field(default_factory=list)
    ausgaben: list[Zeile] = field(default_factory=list)
    anlagen: list[Anlage] = field(default_factory=list)
    monate: list[dict] = field(default_factory=list)
    hinweise: list[str] = field(default_factory=list)

    @property
    def summe_einnahmen(self) -> int:
        return sum(z.cent for z in self.einnahmen)

    @property
    def summe_ausgaben(self) -> int:
        return sum(z.cent for z in self.ausgaben)

    @property
    def gewinn(self) -> int:
        return self.summe_einnahmen - self.summe_ausgaben


def _runden(wert: Decimal) -> int:
    return int(wert.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def afa_plan(ak_cent: int, nutzungsdauer: int, anschaffung: str) -> dict[int, int]:
    """Lineare AfA, monatsgenau ab dem Anschaffungsmonat (pro rata temporis).
    -> {Jahr: AfA in Cent}; die Summe ergibt exakt die Anschaffungskosten."""
    j0, m0 = int(anschaffung[:4]), int(anschaffung[5:7])
    gesamt = nutzungsdauer * 12
    monate: dict[int, int] = {}
    for i in range(gesamt):
        jahr = j0 + (m0 - 1 + i) // 12
        monate[jahr] = monate.get(jahr, 0) + 1
    plan, kumuliert_monate, bisher = {}, 0, 0
    for jahr in sorted(monate):
        kumuliert_monate += monate[jahr]
        kumuliert = _runden(Decimal(ak_cent) * kumuliert_monate / gesamt)
        plan[jahr] = kumuliert - bisher
        bisher = kumuliert
    return plan


def jahre(con) -> list[int]:
    """Alle Jahre ab 2024 bis heute, dazu weitere Jahre mit Belegen."""
    from ..belege.service import ERSTES_JAHR
    js = {int(r[0]) for r in con.execute(
        "SELECT DISTINCT substr(zahlungsdatum, 1, 4) FROM belege WHERE zahlungsdatum IS NOT NULL")}
    js |= {int(r[0]) for r in con.execute("SELECT DISTINCT substr(belegdatum, 1, 4) FROM belege")}
    js |= set(range(ERSTES_JAHR, max(date.today().year, ERSTES_JAHR) + 1))
    return sorted(js, reverse=True)


def berechnen(con, jahr: int, *, nur_festgeschrieben: bool = False) -> Ergebnis:
    s = con.execute("SELECT kleinunternehmer FROM stammdaten LIMIT 1").fetchone()
    ku = bool(s and s["kleinunternehmer"])
    erg = Ergebnis(jahr=jahr, kleinunternehmer=ku, nur_festgeschrieben=nur_festgeschrieben)
    j = str(jahr)
    status = "('festgeschrieben', 'storniert')" if nur_festgeschrieben else "('festgeschrieben', 'storniert', 'entwurf')"
    gebucht = con.execute(f"SELECT * FROM belege WHERE status IN {status}").fetchall()

    def aufwand(b) -> int:
        """Als Betriebsausgabe anzusetzender Betrag eines Ausgabenbelegs (netto bzw. brutto bei KU)."""
        return b["brutto_cent"] if ku else b["netto_cent"]

    ein_kat: dict[str, int] = {}
    aus_kat: dict[str, int] = {}
    ust_ein = ust_erstattung = vorsteuer = ust_gezahlt = gwg = afa = 0
    monate = [{"monat": MONATE[i], "einnahmen": 0, "ausgaben": 0, "ust": 0, "vorsteuer": 0} for i in range(12)]

    for b in gebucht:
        bezahlt_im_jahr = bool(b["zahlungsdatum"]) and b["zahlungsdatum"][:4] == j
        k = b["kategorie"]
        if b["art"] == "einnahme":
            if not bezahlt_im_jahr:
                continue
            m = monate[int(b["zahlungsdatum"][5:7]) - 1]
            m["einnahmen"] += b["brutto_cent"]
            if k == "ust_erstattung":
                ust_erstattung += b["brutto_cent"]
            else:
                ein_kat[k] = ein_kat.get(k, 0) + b["netto_cent"]
                ust_ein += b["ust_cent"]
                m["ust"] += b["ust_cent"]
            continue

        # Ausgaben: Zahlung (Vorsteuer, laufende Kosten)
        if bezahlt_im_jahr:
            m = monate[int(b["zahlungsdatum"][5:7]) - 1]
            m["ausgaben"] += b["brutto_cent"]
            if k == "ust_zahlung":
                ust_gezahlt += b["brutto_cent"]
            else:
                if not ku:
                    vorsteuer += b["ust_cent"]
                    m["vorsteuer"] += b["ust_cent"]
                if k not in kategorien.NACH_ANSCHAFFUNG:
                    aus_kat[k] = aus_kat.get(k, 0) + aufwand(b)
        # Anschaffung (GWG, Anlagevermögen): stornierte Paare bleiben außen vor
        if k in kategorien.NACH_ANSCHAFFUNG and b["status"] in ("festgeschrieben", "entwurf") and b["quelle"] != "storno":
            if k == "gwg":
                if b["belegdatum"][:4] == j:
                    gwg += aufwand(b)
            elif b["nutzungsdauer"]:
                plan = afa_plan(aufwand(b), b["nutzungsdauer"], b["belegdatum"])
                im_jahr = plan.get(jahr, 0)
                bisher = sum(v for jj, v in plan.items() if jj < jahr)
                if bisher < aufwand(b) and int(b["belegdatum"][:4]) <= jahr:
                    afa += im_jahr
                    erg.anlagen.append(Anlage(b["id"], b["belegnummer"] or f"Entwurf #{b['id']}", b["beschreibung"], b["belegdatum"],
                                              aufwand(b), b["nutzungsdauer"], bisher, im_jahr,
                                              aufwand(b) - bisher - im_jahr))

    # --- Betriebseinnahmen
    for k, text in kategorien.EINNAHMEN.items():
        if k != "ust_erstattung" and ein_kat.get(k):
            erg.einnahmen.append(Zeile(k, text, ein_kat[k]))
    if ust_ein:
        erg.einnahmen.append(Zeile("ust_vereinnahmt", "Vereinnahmte Umsatzsteuer", ust_ein))
    if ust_erstattung:
        erg.einnahmen.append(Zeile("ust_erstattung", kategorien.EINNAHMEN["ust_erstattung"], ust_erstattung))

    # --- Betriebsausgaben
    for k, text in kategorien.AUSGABEN.items():
        if k in kategorien.NACH_ANSCHAFFUNG or k == "ust_zahlung" or not aus_kat.get(k):
            continue
        if k == "bewirtung":
            abziehbar = _runden(Decimal(aus_kat[k]) * kategorien.BEWIRTUNG_ABZIEHBAR / 100)
            erg.ausgaben.append(Zeile(k, "Bewirtungskosten (abziehbarer Teil, 70 %)", abziehbar,
                                      f"Gesamt {_euro(aus_kat[k])}, nicht abziehbar {_euro(aus_kat[k] - abziehbar)}"))
        else:
            erg.ausgaben.append(Zeile(k, text, aus_kat[k]))
    if afa:
        erg.ausgaben.append(Zeile("afa", "Absetzung für Abnutzung (AfA) auf Anlagevermögen", afa,
                                  "siehe Anlagenverzeichnis"))
    if gwg:
        erg.ausgaben.append(Zeile("gwg", "Geringwertige Wirtschaftsgüter (Sofortabzug)", gwg))
    if vorsteuer:
        erg.ausgaben.append(Zeile("vorsteuer", "Gezahlte Vorsteuerbeträge", vorsteuer))
    if ust_gezahlt:
        erg.ausgaben.append(Zeile("ust_zahlung", "An das Finanzamt gezahlte Umsatzsteuer", ust_gezahlt))

    erg.monate = monate

    # --- Hinweise auf Belege, die (noch) nicht enthalten sind
    entwuerfe = con.execute("SELECT COUNT(*) FROM belege WHERE status = 'entwurf' AND "
                            "(substr(belegdatum, 1, 4) = ? OR substr(zahlungsdatum, 1, 4) = ?)", (j, j)).fetchone()[0]
    if entwuerfe and nur_festgeschrieben:
        erg.hinweise.append(f"{entwuerfe} Beleg(e) des Jahres sind noch Entwürfe und daher nicht enthalten.")
    elif entwuerfe:
        erg.hinweise.append(f"{entwuerfe} Beleg(e) des Jahres sind noch nicht festgeschrieben (Entwurf), aber enthalten.")
    offen = con.execute("SELECT COUNT(*) FROM belege WHERE status " + ("= 'festgeschrieben'" if nur_festgeschrieben else "IN ('festgeschrieben', 'entwurf')") + " AND quelle <> 'storno' "
                        "AND zahlungsdatum IS NULL AND belegdatum <= ? AND kategorie NOT IN ('gwg', 'anlagevermoegen')",
                        (f"{j}-12-31",)).fetchone()[0]
    if offen:
        erg.hinweise.append(f"{offen} festgeschriebene(r) Beleg(e) ohne Zahlungsdatum – erst mit der Zahlung enthalten.")
    ohne_nd = con.execute("SELECT COUNT(*) FROM belege WHERE status IN ('festgeschrieben', 'entwurf') AND kategorie = 'anlagevermoegen' "
                          "AND (nutzungsdauer IS NULL OR nutzungsdauer < 1)").fetchone()[0]
    if ohne_nd:
        erg.hinweise.append(f"{ohne_nd} Anlagegut/-güter ohne Nutzungsdauer – keine AfA berechnet.")
    return erg


def _euro(c: int) -> str:
    from .. import zahlen
    return zahlen.geld(Decimal(c) / 100)


def csv_export(erg: Ergebnis) -> str:
    import csv
    import io
    puffer = io.StringIO()
    w = csv.writer(puffer, delimiter=";", lineterminator="\r\n")

    def betrag(c: int) -> str:
        return _euro(c).replace(" €", "").replace(".", "")

    w.writerow([f"Einnahmen-Überschuss-Rechnung {erg.jahr}"
                + (" (nur festgeschriebene Belege)" if erg.nur_festgeschrieben else " (einschließlich Entwürfe)")])
    w.writerow([])
    w.writerow(["Bereich", "Position", "Betrag", "Hinweis"])
    for z in erg.einnahmen:
        w.writerow(["Betriebseinnahmen", z.text, betrag(z.cent), z.hinweis])
    w.writerow(["Betriebseinnahmen", "Summe", betrag(erg.summe_einnahmen), ""])
    for z in erg.ausgaben:
        w.writerow(["Betriebsausgaben", z.text, betrag(z.cent), z.hinweis])
    w.writerow(["Betriebsausgaben", "Summe", betrag(erg.summe_ausgaben), ""])
    w.writerow(["Ergebnis", "Gewinn / Verlust", betrag(erg.gewinn), ""])
    if erg.anlagen:
        w.writerow([])
        w.writerow(["Anlagenverzeichnis", "Beleg", "Bezeichnung", "Anschaffung", "AK", "ND (Jahre)",
                    "AfA bisher", f"AfA {erg.jahr}", "Restwert 31.12."])
        for a in erg.anlagen:
            w.writerow(["", a.belegnummer, a.beschreibung, a.anschaffung, betrag(a.ak_cent), a.nutzungsdauer,
                        betrag(a.afa_bisher), betrag(a.afa_jahr), betrag(a.restwert)])
    return "﻿" + puffer.getvalue()
