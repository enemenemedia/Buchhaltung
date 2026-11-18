"""Tags für Belege, Auswertung und CSV nach Tags, Jahre ab 2024."""

from datetime import date

import pytest

from app import create_app, db
from app.belege import auswertung, service, tags

STANDARD = ["Fahrtkosten", "Fremdleistung", "Büromaterial", "Internet/Kommunikation", "Abo", "Software", "Waren",
            "Reparaturen", "sonstige Betriebskosten"]


@pytest.fixture
def app(tmp_path):
    return create_app(tmp_path)


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def con(app):
    c = db.verbinden(app.config["DATENBANK"])
    yield c
    c.close()


def tag_id(con, name):
    return con.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()[0]


def erfassen(client, con, *tagnamen, neue="", **w):
    daten = {"art": "ausgabe", "belegdatum": "2024-03-05", "partner": "Lieferant", "beschreibung": "Test",
             "kategorie": "sonstige_ausgaben", "ust_satz": "19", "netto": "100", **w}
    daten["tags"] = [str(tag_id(con, n)) for n in tagnamen]
    daten["neue_tags"] = neue
    r = client.post("/belege/neu", data=daten)
    assert r.status_code == 302, r.get_data(as_text=True)[:400]
    return int(r.headers["Location"].rstrip("/").split("/")[-1])


def namen(con, bid):
    return [t["name"] for t in tags.fuer_beleg(con, bid)]


def test_standard_tags_einmalig(app, con, tmp_path):
    assert [t["name"] for t in tags.alle(con)] == STANDARD
    tags.aendern(con, tag_id(con, "Abo"), "Abonnements", None, True)
    tags.aendern(con, tag_id(con, "Waren"), "Waren", "waren", False)
    con.commit()
    create_app(tmp_path)                           # erneuter Start: keine Standard-Tags nachlegen
    alle = [t["name"] for t in tags.alle(con)]
    assert "Abo" not in alle and "Abonnements" in alle and len(alle) == 9
    assert "Waren" not in [t["name"] for t in tags.alle(con, nur_aktive=True)]


def test_tags_beim_erfassen_und_neue_tags(client, con):
    bid = erfassen(client, con, "Büromaterial", "Abo", neue="Messe, büromaterial")
    assert namen(con, bid) == ["Büromaterial", "Abo", "Messe"]       # „büromaterial“ = vorhandener Tag
    assert tags.alle(con)[-1]["name"] == "Messe"
    html = client.get(f"/belege/{bid}").get_data(as_text=True)
    assert "Messe" in html and 'class="tag-chip gewaehlt"' in html


def test_ungueltiger_tag(client, con):
    with pytest.raises(tags.TagFehler):
        tags.anlegen(con, " ")
    with pytest.raises(tags.TagFehler):
        tags.anlegen(con, "x" * 61)
    with pytest.raises(tags.TagFehler):
        tags.aendern(con, tag_id(con, "Abo"), "Software", None, True)          # doppelt


def test_tags_auch_nach_festschreiben_aenderbar(client, con):
    bid = erfassen(client, con, "Software", aktion="festschreiben")
    assert service.laden(con, bid)["status"] == "festgeschrieben"
    r = client.post(f"/belege/{bid}/tags", data={"tags": [str(tag_id(con, "Abo"))], "neue_tags": "Lizenz"})
    assert r.status_code == 302
    assert namen(con, bid) == ["Abo", "Lizenz"]
    eintrag = con.execute("SELECT * FROM aenderungsprotokoll WHERE bereich = 'beleg' AND objekt_id = ? "
                          "AND aktion = 'tags' ORDER BY id DESC", (bid,)).fetchone()
    assert "Software" in eintrag["details"] and "Abo, Lizenz" in eintrag["details"]


def test_filter_export_loeschen_storno(client, con):
    a = erfassen(client, con, "Fahrtkosten", aktion="festschreiben")
    b = erfassen(client, con)                                            # ohne Tag
    html = client.get(f"/belege/?jahr=2024&tag={tag_id(con, 'Fahrtkosten')}").get_data(as_text=True)
    assert f"/belege/{a}" in html and f"/belege/{b}\"" not in html
    assert f"/belege/{b}" in client.get("/belege/?jahr=2024&tag=ohne").get_data(as_text=True)
    csv = client.get("/belege/export.csv?jahr=2024").get_data(as_text=True)
    assert ";Tags;" in csv.splitlines()[0] and ";Fahrtkosten;" in csv
    # Storno übernimmt die Tags
    sid = service.stornieren(con, a, "falsch")
    con.commit()
    assert namen(con, sid) == ["Fahrtkosten"]
    # Löschen eines Entwurfs entfernt seine Tag-Zuordnung
    client.post(f"/belege/{b}/tags", data={"tags": [str(tag_id(con, "Abo"))]})
    client.post(f"/belege/{b}/loeschen")
    assert con.execute("SELECT COUNT(*) FROM beleg_tags WHERE beleg_id = ?", (b,)).fetchone()[0] == 0


def test_tag_verwaltung_ueber_oberflaeche(client, con):
    assert client.post("/belege/tags/neu", data={"name": "Messe", "kategorie": "werbung"}).status_code == 302
    t = con.execute("SELECT * FROM tags WHERE name = 'Messe'").fetchone()
    assert t["kategorie"] == "werbung" and t["aktiv"] == 1
    client.post(f"/belege/tags/{t['id']}", data={"name": "Messen", "kategorie": ""})        # ohne „aktiv“
    t = con.execute("SELECT * FROM tags WHERE id = ?", (t["id"],)).fetchone()
    assert (t["name"], t["kategorie"], t["aktiv"]) == ("Messen", None, 0)
    html = client.get("/belege/tags/").get_data(as_text=True)
    assert "Messen" in html and "Reparaturen" in html
    assert "Messen" not in client.get("/belege/neu").get_data(as_text=True)                 # ausgeblendet


def test_auswertung_nach_tags(client, con):
    erfassen(client, con, "Büromaterial", netto="100", aktion="festschreiben")
    erfassen(client, con, "Büromaterial", "Software", netto="50")                          # Entwurf, 2 Tags
    erfassen(client, con, netto="10", zahlungsdatum="2025-01-10")                           # ohne Tag
    erfassen(client, con, "Software", netto="999", belegdatum="2025-02-01")                  # anderes Jahr
    s = erfassen(client, con, "Büromaterial", netto="70", aktion="festschreiben")
    service.stornieren(con, s, "doppelt")                                                    # Paar bleibt außen vor
    con.commit()

    a = auswertung.berechnen(con, 2024)
    g = {x.tag: x for x in a.gruppen}
    assert (g["Büromaterial"].anzahl, g["Büromaterial"].netto, g["Büromaterial"].brutto) == (2, 15000, 17850)
    assert (g["Software"].anzahl, g["Software"].netto) == (1, 5000)
    assert (g[auswertung.OHNE_TAG].anzahl, g[auswertung.OHNE_TAG].netto) == (1, 1000)
    assert g["Abo"].anzahl == 0
    assert (a.gesamt.anzahl, a.gesamt.netto) == (3, 16000)       # jeder Beleg einmal
    assert (a.entwuerfe, a.mehrfach) == (2, 1)

    fest = auswertung.berechnen(con, 2024, nur_festgeschrieben=True)
    assert (fest.gesamt.anzahl, fest.gesamt.netto) == (1, 10000)
    nach_zahlung = auswertung.berechnen(con, 2025, datum="zahlung")
    assert nach_zahlung.gesamt.netto == 1000

    html = client.get("/belege/auswertung?jahr=2024").get_data(as_text=True)
    assert "Ausgaben nach Tags 2024" in html and "178,50 €" in html and "einschließlich Entwürfe" in html


def test_csv_nach_tags(client, con):
    erfassen(client, con, "Büromaterial", netto="100")
    erfassen(client, con, "Büromaterial", "Software", netto="50", partner="Adobe")
    erfassen(client, con, netto="10.5")
    r = client.get("/belege/auswertung.csv?jahr=2024")
    assert r.headers["Content-Type"] == "text/csv; charset=utf-8"
    assert "belege_nach_tags_ausgabe_2024.csv" in r.headers["Content-Disposition"]
    z = r.get_data(as_text=True).lstrip("﻿").splitlines()
    assert z[0].startswith("Belege nach Tags – Ausgaben 2024 nach Belegdatum, einschließlich Entwürfe")
    zeilen = [x.split(";") for x in z]
    buero = [x for x in zeilen if x[0] == "Büromaterial" and len(x) > 5]
    assert len(buero) == 2 and buero[1][9] == "Büromaterial, Software"
    summe = next(x for x in zeilen if x[0] == "Summe Büromaterial")
    assert summe[10:] == ["150,00", "28,50", "178,50"]
    assert next(x for x in zeilen if x[0] == "Summe Software")[10:] == ["50,00", "9,50", "59,50"]
    assert next(x for x in zeilen if x[0] == "Summe (ohne Tag)")[10:] == ["10,50", "2,00", "12,50"]
    i = z.index("Übersicht;Anzahl;Netto;USt;Brutto")
    assert "Büromaterial;2;150,00;28,50;178,50" in z[i:] and "Abo;0;0,00;0,00;0,00" in z[i:]
    assert "Gesamt (jeder Beleg einmal);3;160,50;30,50;191,00" in z[i:]
    assert any("mehrere Tags" in x for x in z)


def test_jahre_ab_2024(client, con):
    js = service.jahre(con)
    assert "2024" in js and "2025" in js and str(date.today().year) in js
    assert service.standard_jahr(con) == "2024"
    assert 'selected>2024' in client.get("/belege/").get_data(as_text=True).replace("\n", "")
    erfassen(client, con, belegdatum="2025-06-01")
    assert service.standard_jahr(con) == "2025"
    # „danach nächsten Beleg erfassen“ übernimmt das Datum
    r = client.post("/belege/neu", data={"art": "ausgabe", "belegdatum": "2024-07-01", "partner": "X",
                                         "beschreibung": "Y", "kategorie": "buero", "ust_satz": "19",
                                         "netto": "1", "weiter": "1"})
    assert "datum=2024-07-01" in r.headers["Location"]
    assert 'value="2024-07-01"' in client.get(r.headers["Location"]).get_data(as_text=True)
