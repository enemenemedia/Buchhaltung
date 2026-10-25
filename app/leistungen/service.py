"""Fachlogik des Leistungsverzeichnisses.

Jede Leistung hat eine Art (Zeiteinheit, Stück oder pauschal), eine Einheit,
einen Netto-Einzelpreis und eine Standard-Anzahl. Alle Änderungen werden im
Änderungsprotokoll festgehalten.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from decimal import Decimal

from .. import protokoll, zahlen

BEREICH = "leistung"


@dataclass(frozen=True)
class Einheit:
    code: str        # UN/ECE Recommendation 20 (XRechnung BT-130)
    name: str        # Singular
    mehrzahl: str


# Art -> erlaubte Einheiten (erste = Vorgabe)
EINHEITEN: dict[str, tuple[Einheit, ...]] = {
    "zeit": (
        Einheit("HUR", "Stunde", "Stunden"),
        Einheit("MIN", "Minute", "Minuten"),
        Einheit("DAY", "Tag", "Tage"),
        Einheit("WEE", "Woche", "Wochen"),
        Einheit("MON", "Monat", "Monate"),
    ),
    "stueck": (Einheit("H87", "Stück", "Stück"),),
    "pauschal": (Einheit("LS", "Pauschale", "Pauschalen"),),
}

ARTEN = {"zeit": "Zeiteinheit", "stueck": "Stück", "pauschal": "Pauschal"}
UST_SAETZE = ("19", "7", "0")

EINHEIT_NACH_CODE = {e.code: e for liste in EINHEITEN.values() for e in liste}
ART_NACH_CODE = {e.code: art for art, liste in EINHEITEN.items() for e in liste}

FELDER = ("nummer", "bezeichnung", "beschreibung", "kategorie", "art", "einheit_code",
          "preis", "anzahl", "ust_satz")

BEZEICHNUNG = {
    "nummer": "Nummer", "bezeichnung": "Bezeichnung", "beschreibung": "Beschreibung",
    "kategorie": "Kategorie", "art": "Art", "einheit_code": "Einheit", "preis": "Einzelpreis",
    "anzahl": "Anzahl", "ust_satz": "USt-Satz", "aktiv": "Aktiv",
}

MAX_PREIS = Decimal("10000000")
MAX_ANZAHL = Decimal("1000000")


class LeistungsFehler(Exception):
    """Fachlicher Fehler, der dem Anwender angezeigt werden kann."""


class ValidierungsFehler(LeistungsFehler):
    def __init__(self, fehler: dict[str, str]):
        super().__init__("Bitte die markierten Felder prüfen.")
        self.fehler = fehler


# ---------------------------------------------------------------------------
# Hilfen
# ---------------------------------------------------------------------------

def einheit_name(code: str, anzahl=None) -> str:
    e = EINHEIT_NACH_CODE.get(code)
    if e is None:
        return code
    if anzahl is not None and zahlen.parsen(anzahl) != 1:
        return e.mehrzahl
    return e.name


def gesamt(preis, anzahl) -> Decimal:
    return zahlen.runden(zahlen.parsen(preis) * zahlen.parsen(anzahl))


def naechste_nummer(con: sqlite3.Connection) -> str:
    """Nächste freie Nummer im Schema L0001, L0002, …"""
    hoechste = 0
    for (nr,) in con.execute("SELECT nummer FROM leistungen WHERE nummer LIKE 'L%'"):
        m = re.fullmatch(r"L(\d+)", nr, flags=re.I)
        if m:
            hoechste = max(hoechste, int(m.group(1)))
    return f"L{hoechste + 1:04d}"


# ---------------------------------------------------------------------------
# Normalisieren & Prüfen
# ---------------------------------------------------------------------------

def normalisieren(roh: dict) -> tuple[dict, dict[str, str]]:
    """Bereitet Eingaben (Formular oder Import) auf und prüft sie.

    Liefert (daten, fehler). Zahlen werden in Speicherform zurückgegeben.
    """
    d = {f: str(roh.get(f) if roh.get(f) is not None else "").strip() for f in FELDER}
    fehler: dict[str, str] = {}

    d["nummer"] = re.sub(r"\s+", "", d["nummer"])
    if len(d["nummer"]) > 30:
        fehler["nummer"] = "Die Nummer darf höchstens 30 Zeichen haben."
    if not d["bezeichnung"]:
        fehler["bezeichnung"] = "Bitte eine Bezeichnung angeben."
    elif len(d["bezeichnung"]) > 200:
        fehler["bezeichnung"] = "Die Bezeichnung darf höchstens 200 Zeichen haben."

    if d["art"] not in EINHEITEN:
        fehler["art"] = "Bitte Zeiteinheit, Stück oder Pauschal wählen."
    else:
        erlaubt = [e.code for e in EINHEITEN[d["art"]]]
        if not d["einheit_code"]:
            d["einheit_code"] = erlaubt[0]
        if d["einheit_code"] not in erlaubt:
            fehler["einheit_code"] = "Die Einheit passt nicht zur gewählten Art."

    for feld, name, grenze, null_ok in (("preis", "Preis", MAX_PREIS, True),
                                         ("anzahl", "Anzahl", MAX_ANZAHL, False)):
        roh_wert = d[feld] or ("1" if feld == "anzahl" else "")
        try:
            zahl = zahlen.parsen(roh_wert, feld=name)
        except zahlen.ZahlFehler as e:
            fehler[feld] = str(e)
            continue
        if zahl < 0 or (zahl == 0 and not null_ok):
            fehler[feld] = f"{name} muss größer als 0 sein." if not null_ok else f"{name} darf nicht negativ sein."
        elif zahl >= grenze:
            fehler[feld] = f"{name} ist zu groß."
        elif zahl.as_tuple().exponent < (-4 if feld == "preis" else -3):
            fehler[feld] = f"{name}: höchstens {4 if feld == 'preis' else 3} Nachkommastellen."
        else:
            d[feld] = zahlen.speicherform(zahl)

    ust = d["ust_satz"].replace("%", "").replace(",", ".").strip() or "19"
    if "." in ust:
        ust = ust.rstrip("0").rstrip(".") or "0"
    if ust not in UST_SAETZE:
        fehler["ust_satz"] = "USt-Satz 19, 7 oder 0 %."
    d["ust_satz"] = ust
    return d, fehler


# ---------------------------------------------------------------------------
# Lesen
# ---------------------------------------------------------------------------

def liste(con: sqlite3.Connection, suche: str = "", art: str = "", kategorie: str = "",
          status: str = "aktiv") -> list[sqlite3.Row]:
    sql = "SELECT * FROM leistungen WHERE 1 = 1"
    params: list = []
    if status == "aktiv":
        sql += " AND aktiv = 1"
    elif status == "inaktiv":
        sql += " AND aktiv = 0"
    if art in EINHEITEN:
        sql += " AND art = ?"
        params.append(art)
    if kategorie:
        sql += " AND kategorie = ?"
        params.append(kategorie)
    if suche:
        sql += " AND (nummer LIKE ? OR bezeichnung LIKE ? OR beschreibung LIKE ? OR kategorie LIKE ?)"
        params += [f"%{suche}%"] * 4
    sql += " ORDER BY kategorie COLLATE NOCASE, nummer COLLATE NOCASE"
    return con.execute(sql, params).fetchall()


def kategorien(con: sqlite3.Connection) -> list[str]:
    return [r[0] for r in con.execute(
        "SELECT DISTINCT kategorie FROM leistungen WHERE kategorie <> '' ORDER BY kategorie COLLATE NOCASE")]


def laden(con: sqlite3.Connection, leistung_id: int) -> sqlite3.Row:
    z = con.execute("SELECT * FROM leistungen WHERE id = ?", (leistung_id,)).fetchone()
    if z is None:
        raise LeistungsFehler("Leistung nicht gefunden.")
    return z


def nach_nummer(con: sqlite3.Connection, nummer: str) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM leistungen WHERE nummer = ?", (nummer,)).fetchone()


def verlauf(con: sqlite3.Connection, leistung_id: int) -> list[dict]:
    return protokoll.lesen(con, BEREICH, leistung_id)


# ---------------------------------------------------------------------------
# Schreiben
# ---------------------------------------------------------------------------

def _anzeige(feld: str, wert) -> str:
    """Lesbare Werte für das Protokoll."""
    if feld == "art":
        return ARTEN.get(wert, wert)
    if feld == "einheit_code":
        return einheit_name(wert)
    if feld == "preis":
        return zahlen.geld(wert)
    if feld == "anzahl":
        return zahlen.menge(wert)
    if feld == "ust_satz":
        return f"{wert} %"
    return wert


def aenderungen_beschreiben(alt, neu: dict) -> dict[str, list]:
    roh = protokoll.felder_diff(alt, neu, BEZEICHNUNG)
    rueck = {v: k for k, v in BEZEICHNUNG.items()}
    return {name: [_anzeige(rueck[name], a), _anzeige(rueck[name], n)] for name, (a, n) in roh.items()}


def _nummer_pruefen(con, d: dict, fehler: dict, eigene_id: int | None) -> None:
    if not d["nummer"]:
        d["nummer"] = naechste_nummer(con)
        return
    vorhanden = nach_nummer(con, d["nummer"])
    if vorhanden and vorhanden["id"] != eigene_id:
        fehler["nummer"] = f"Die Nummer {d['nummer']} ist bereits vergeben."


def anlegen(con: sqlite3.Connection, roh: dict, *, quelle: str = "", commit: bool = True) -> int:
    d, fehler = normalisieren(roh)
    _nummer_pruefen(con, d, fehler, None)
    if fehler:
        raise ValidierungsFehler(fehler)
    jetzt = protokoll.jetzt()
    cur = con.execute(
        f"INSERT INTO leistungen ({', '.join(FELDER)}, erstellt_am, geaendert_am) "
        f"VALUES ({', '.join('?' * len(FELDER))}, ?, ?)",
        [*(d[f] for f in FELDER), jetzt, jetzt],
    )
    leer = {f: "" for f in FELDER}
    details: dict = {"felder": aenderungen_beschreiben(leer, d)}
    if quelle:
        details["hinweise"] = [f"Importiert aus {quelle}"]
    protokoll.schreiben(con, BEREICH, cur.lastrowid, "angelegt", details)
    if commit:
        con.commit()
    return cur.lastrowid


def aendern(con: sqlite3.Connection, leistung_id: int, roh: dict, *, quelle: str = "",
            commit: bool = True) -> bool:
    alt = laden(con, leistung_id)
    d, fehler = normalisieren(roh)
    if not d["nummer"]:
        d["nummer"] = alt["nummer"]
    _nummer_pruefen(con, d, fehler, leistung_id)
    if fehler:
        raise ValidierungsFehler(fehler)
    diff = aenderungen_beschreiben(alt, d)
    if not diff:
        return False
    con.execute(
        f"UPDATE leistungen SET {', '.join(f + ' = ?' for f in FELDER)}, geaendert_am = ? WHERE id = ?",
        [*(d[f] for f in FELDER), protokoll.jetzt(), leistung_id],
    )
    details: dict = {"felder": diff}
    if quelle:
        details["hinweise"] = [f"Aktualisiert durch Import aus {quelle}"]
    protokoll.schreiben(con, BEREICH, leistung_id, "geaendert", details)
    if commit:
        con.commit()
    return True


def aktiv_setzen(con: sqlite3.Connection, leistung_id: int, aktiv: bool) -> None:
    alt = laden(con, leistung_id)
    if bool(alt["aktiv"]) == aktiv:
        return
    with con:
        con.execute("UPDATE leistungen SET aktiv = ?, geaendert_am = ? WHERE id = ?",
                    (int(aktiv), protokoll.jetzt(), leistung_id))
        protokoll.schreiben(con, BEREICH, leistung_id, "aktiviert" if aktiv else "deaktiviert")


def loeschen(con: sqlite3.Connection, leistung_id: int) -> sqlite3.Row:
    """Löscht eine Leistung. Das Protokoll bleibt erhalten.

    Angebote und Rechnungen übernehmen später die Werte der Leistung als
    Kopie, sodass bestehende Belege durch das Löschen nicht verändert werden.
    """
    alt = laden(con, leistung_id)
    with con:
        con.execute("DELETE FROM leistungen WHERE id = ?", (leistung_id,))
        protokoll.schreiben(con, BEREICH, leistung_id, "geloescht",
                            {"hinweise": [f"{alt['nummer']} – {alt['bezeichnung']}"]})
    return alt
