"""Tags für Belege: frei erweiterbare Schlagworte, mehrere je Beleg.

Tags ordnen Belege zusätzlich zur EÜR-Kategorie (z. B. „Abo“, „Reparaturen“). Sie gehören nicht
zum festgeschriebenen Inhalt eines Belegs und können deshalb jederzeit geändert werden – jede
Änderung steht im Änderungsprotokoll des Belegs. Tags werden nie gelöscht, nur ausgeblendet,
damit sie an älteren Belegen erhalten bleiben.
"""

from __future__ import annotations

import re

from .. import protokoll
from . import kategorien

BEREICH = "beleg"
MAX_LAENGE = 60


class TagFehler(Exception):
    pass


def _name_pruefen(name: str) -> str:
    name = re.sub(r"\s+", " ", (name or "").strip())
    if not name:
        raise TagFehler("Bitte einen Namen für den Tag angeben.")
    if len(name) > MAX_LAENGE:
        raise TagFehler(f"Der Name ist zu lang (höchstens {MAX_LAENGE} Zeichen).")
    if any(z in name for z in ",;"):
        raise TagFehler("Komma und Semikolon sind im Namen eines Tags nicht erlaubt.")
    return name


def alle(con, *, nur_aktive: bool = False) -> list:
    """Alle Tags mit der Zahl ihrer Belege, sortiert nach Reihenfolge und Name."""
    sql = """SELECT t.*, (SELECT COUNT(*) FROM beleg_tags bt WHERE bt.tag_id = t.id) AS anzahl
             FROM tags t""" + (" WHERE t.aktiv = 1" if nur_aktive else "") + " ORDER BY t.reihenfolge, t.name"
    return con.execute(sql).fetchall()


def laden(con, tag_id: int):
    t = con.execute("SELECT * FROM tags WHERE id = ?", (tag_id,)).fetchone()
    if t is None:
        raise TagFehler("Tag nicht gefunden.")
    return t


def anlegen(con, name: str, kategorie: str | None = None) -> int:
    """Legt einen Tag an. Gibt es ihn schon (ohne Rücksicht auf Groß-/Kleinschreibung),
    wird der vorhandene zurückgegeben und ggf. wieder eingeblendet."""
    name = _name_pruefen(name)
    if kategorie and kategorie not in kategorien.ALLE:
        raise TagFehler("Unbekannte Kategorie.")
    vorhanden = con.execute("SELECT id, aktiv FROM tags WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
    if vorhanden:
        if not vorhanden["aktiv"]:
            con.execute("UPDATE tags SET aktiv = 1 WHERE id = ?", (vorhanden["id"],))
        return vorhanden["id"]
    reihenfolge = (con.execute("SELECT MAX(reihenfolge) FROM tags").fetchone()[0] or 0) + 10
    return con.execute("INSERT INTO tags (name, kategorie, reihenfolge) VALUES (?, ?, ?)",
                       (name, kategorie or None, reihenfolge)).lastrowid


def aendern(con, tag_id: int, name: str, kategorie: str | None, aktiv: bool) -> None:
    laden(con, tag_id)
    name = _name_pruefen(name)
    if kategorie and kategorie not in kategorien.ALLE:
        raise TagFehler("Unbekannte Kategorie.")
    doppelt = con.execute("SELECT 1 FROM tags WHERE name = ? COLLATE NOCASE AND id <> ?", (name, tag_id)).fetchone()
    if doppelt:
        raise TagFehler(f"Einen Tag „{name}“ gibt es bereits.")
    con.execute("UPDATE tags SET name = ?, kategorie = ?, aktiv = ? WHERE id = ?",
                (name, kategorie or None, 1 if aktiv else 0, tag_id))


def namen_zerlegen(text: str) -> list[str]:
    """„Messe, Druck; Porto“ -> ["Messe", "Druck", "Porto"]"""
    return [t.strip() for t in re.split(r"[,;]", text or "") if t.strip()]


def fuer_beleg(con, beleg_id: int) -> list:
    return con.execute("""SELECT t.* FROM tags t JOIN beleg_tags bt ON bt.tag_id = t.id
                          WHERE bt.beleg_id = ? ORDER BY t.reihenfolge, t.name""", (beleg_id,)).fetchall()


def fuer_belege(con, beleg_ids) -> dict[int, list[str]]:
    """{beleg_id: [Tagname, …]} für viele Belege in einer Abfrage."""
    ids = list(beleg_ids)
    erg: dict[int, list[str]] = {i: [] for i in ids}
    for i in range(0, len(ids), 500):
        teil = ids[i:i + 500]
        for z in con.execute(f"""SELECT bt.beleg_id, t.name FROM beleg_tags bt JOIN tags t ON t.id = bt.tag_id
                                 WHERE bt.beleg_id IN ({','.join('?' * len(teil))})
                                 ORDER BY t.reihenfolge, t.name""", teil):
            erg[z["beleg_id"]].append(z["name"])
    return erg


def setzen(con, beleg_id: int, tag_ids, neue_namen: str = "", *, protokollieren: bool = True) -> list[str]:
    """Setzt die Tags eines Belegs (Auswahl + neu eingetippte). -> Namen der Tags danach."""
    ids: set[int] = set()
    for t in tag_ids or []:
        try:
            ids.add(laden(con, int(t))["id"])
        except (TypeError, ValueError, TagFehler):
            continue
    for name in namen_zerlegen(neue_namen):
        ids.add(anlegen(con, name))
    alt = [t["name"] for t in fuer_beleg(con, beleg_id)]
    con.execute("DELETE FROM beleg_tags WHERE beleg_id = ?", (beleg_id,))
    con.executemany("INSERT INTO beleg_tags (beleg_id, tag_id) VALUES (?, ?)", [(beleg_id, i) for i in sorted(ids)])
    neu = [t["name"] for t in fuer_beleg(con, beleg_id)]
    if protokollieren and alt != neu:
        protokoll.schreiben(con, BEREICH, beleg_id, "tags",
                            {"felder": {"Tags": [", ".join(alt) or None, ", ".join(neu) or None]}})
    return neu


def kopieren(con, von_beleg: int, nach_beleg: int) -> None:
    con.execute("INSERT OR IGNORE INTO beleg_tags (beleg_id, tag_id) SELECT ?, tag_id FROM beleg_tags WHERE beleg_id = ?",
                (nach_beleg, von_beleg))
