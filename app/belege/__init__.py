"""Modul Belegerfassung: Einnahmen und Ausgaben mit Belegdatei, Tags, Festschreiben, Storno, Auswertung, Export."""

from __future__ import annotations

from datetime import date

from flask import (Blueprint, Response, abort, current_app, flash, redirect, render_template, request,
                   send_file, url_for)

from .. import zahlen
from ..db import get_db
from . import abgleich, auswertung, kategorien, service
from . import tags as tagmod

bp = Blueprint("belege", __name__, url_prefix="/belege")


def rechnungen_abgleichen() -> None:
    """Neue abgeschlossene Rechnungen und Zahlungseingänge übernehmen (auch für die EÜR)."""
    con = get_db()
    erg = abgleich.abgleichen(con)
    n = len(erg["neu"])
    if n:
        flash(f"{n} abgeschlossene Rechnung{'en' if n > 1 else ''} als Einnahme übernommen.", "ok")
    z = len(erg["zahlungen"])
    if z:
        flash(f"{z} Zahlungseingang{'' if z == 1 else 'e'} aus „Rechnungen“ übernommen.", "ok")


@bp.app_template_filter("cent")
def _cent_filter(c) -> str:
    return zahlen.geld(service.als_euro(c))


@bp.app_template_filter("kategorie")
def _kategorie_filter(k) -> str:
    return kategorien.bezeichnung(k)


def _daten():
    return current_app.config["DATENVERZEICHNIS"]


def _datei_aus_anfrage() -> tuple[bytes, str] | None:
    f = request.files.get("datei")
    if not f or not f.filename:
        return None
    return f.read(), f.filename


def _formular(beleg: dict, fehler=None, beleg_id=None, status=200, gewaehlt=None, neue_tags=""):
    con = get_db()
    if gewaehlt is None:
        gewaehlt = [t["id"] for t in tagmod.fuer_beleg(con, beleg_id)] if beleg_id else []
    gewaehlt = {int(t) for t in gewaehlt if str(t).isdigit()}
    # aktive Tags und solche, die der Beleg schon trägt (auch ausgeblendete)
    alle = [t for t in tagmod.alle(con) if t["aktiv"] or t["id"] in gewaehlt]
    return render_template("belege/formular.html", beleg=beleg, fehler=fehler or {}, beleg_id=beleg_id,
                           einnahmen=kategorien.EINNAHMEN, ausgaben=kategorien.AUSGABEN,
                           zahlungsarten=service.ZAHLUNGSARTEN, ust_texte=service.UST_TEXT,
                           ohne_ust=sorted(kategorien.OHNE_UST), alle_tags=alle, gewaehlt=gewaehlt,
                           neue_tags=neue_tags,
                           tag_kategorien={t["id"]: t["kategorie"] for t in alle if t["kategorie"]}), status


def _filter() -> service.Filter:
    jahr = request.args.get("jahr")
    if jahr is None:
        jahr = service.standard_jahr(get_db())
    return service.Filter(jahr=jahr, art=request.args.get("art", ""), status=request.args.get("status", ""),
                          suche=request.args.get("q", "").strip(), tag=request.args.get("tag", ""))


def _tags_speichern(con, beleg_id: int) -> None:
    try:
        tagmod.setzen(con, beleg_id, request.form.getlist("tags"), request.form.get("neue_tags", ""))
    except tagmod.TagFehler as e:
        raise service.BelegFehler(str(e)) from None


@bp.get("/")
def liste():
    rechnungen_abgleichen()
    con = get_db()
    flt = _filter()
    belege = service.liste(con, flt)
    return render_template("belege/liste.html", belege=belege, filter=flt, jahre=service.jahre(con),
                           kennzahlen=service.kennzahlen(belege, flt.jahr), STATUS=service.STATUS,
                           tags_je_beleg=tagmod.fuer_belege(con, [b["id"] for b in belege]),
                           alle_tags=tagmod.alle(con))


@bp.route("/neu", methods=["GET", "POST"])
def neu():
    if request.method == "GET":
        art = request.args.get("art", "ausgabe")
        art = art if art in ("einnahme", "ausgabe") else "ausgabe"
        datum = request.args.get("datum", "")
        try:
            date.fromisoformat(datum)
        except ValueError:
            datum = date.today().isoformat()
        return _formular({"art": art, "belegdatum": datum, "ust_satz": "19", "kategorie": kategorien.STANDARD[art]})
    con = get_db()
    gewaehlt, neue = request.form.getlist("tags"), request.form.get("neue_tags", "")
    try:
        werte = service.formular_auswerten(request.form)
        bid = service.anlegen(con, _daten(), werte, _datei_aus_anfrage())
        _tags_speichern(con, bid)
        if request.form.get("aktion") == "festschreiben":
            service.festschreiben(con, bid)
    except service.ValidierungsFehler as e:
        return _formular(e.werte, e.fehler, status=422, gewaehlt=gewaehlt, neue_tags=neue)
    except service.BelegFehler as e:
        con.rollback()
        werte = dict(request.form)
        return _formular(werte, {"datei": str(e)}, status=422, gewaehlt=gewaehlt, neue_tags=neue)
    con.commit()
    _doppelt_warnen(con, bid)
    flash("Beleg gespeichert." if request.form.get("aktion") != "festschreiben"
          else "Beleg gespeichert und festgeschrieben.", "ok")
    if request.form.get("weiter"):
        # Nachtragen (z. B. Belege 2024): Art und Datum für den nächsten Beleg übernehmen
        return redirect(url_for(".neu", art=werte["art"], datum=werte["belegdatum"]))
    return redirect(url_for(".detail", beleg_id=bid))


def _doppelt_warnen(con, bid: int) -> None:
    b = service.laden(con, bid)
    if not b["rechnungsnummer"]:
        return
    n = con.execute("SELECT COUNT(*) FROM belege WHERE id <> ? AND partner = ? AND rechnungsnummer = ? "
                    "AND quelle NOT IN ('storno') AND status <> 'storniert'",
                    (bid, b["partner"], b["rechnungsnummer"])).fetchone()[0]
    if n:
        flash(f"Achtung: Rechnung {b['rechnungsnummer']} von {b['partner']} ist bereits erfasst – "
              "bitte auf Doppelerfassung prüfen.", "fehler")


@bp.post("/xrechnung")
def xrechnung_import():
    datei = request.files.get("xml")
    if not datei or not datei.filename:
        flash("Bitte eine XRechnung-Datei (XML) auswählen.", "fehler")
        return redirect(url_for(".liste"))
    con = get_db()
    try:
        bid, hinweise = service.xrechnung_importieren(con, _daten(), datei.read(), datei.filename)
    except service.BelegFehler as e:
        flash(str(e), "fehler")
        return redirect(url_for(".liste"))
    con.commit()
    for h in hinweise:
        flash(h, "fehler")
    flash("XRechnung eingelesen. Bitte Kategorie und Zahlungsdatum prüfen und den Beleg festschreiben.", "ok")
    return redirect(url_for(".bearbeiten", beleg_id=bid))


@bp.get("/<int:beleg_id>")
def detail(beleg_id: int):
    con = get_db()
    try:
        b = service.laden(con, beleg_id)
    except service.BelegFehler:
        abort(404)
    storno = con.execute("SELECT * FROM belege WHERE storno_von = ?", (beleg_id,)).fetchone()
    original = con.execute("SELECT * FROM belege WHERE id = ?", (b["storno_von"],)).fetchone() if b["storno_von"] else None
    rechnung = None
    if b["rechnung_id"]:
        rechnung = con.execute("SELECT id, nummer, art, dateiname, status FROM rechnungen WHERE id = ?",
                               (b["rechnung_id"],)).fetchone()
    beleg_tags = tagmod.fuer_beleg(con, beleg_id)
    ids = {t["id"] for t in beleg_tags}
    return render_template("belege/detail.html", b=b, storno=storno, original=original, rechnung=rechnung,
                           protokoll=service.protokoll_lesen(con, beleg_id), STATUS=service.STATUS,
                           ust_texte=service.UST_TEXT, zahlungsarten=service.ZAHLUNGSARTEN,
                           beleg_tags=beleg_tags, gewaehlt=ids,
                           alle_tags=[t for t in tagmod.alle(con) if t["aktiv"] or t["id"] in ids])


@bp.post("/<int:beleg_id>/tags")
def tags_aendern(beleg_id: int):
    """Tags dürfen auch an festgeschriebenen Belegen geändert werden (protokolliert)."""
    con = get_db()
    try:
        service.laden(con, beleg_id)
        _tags_speichern(con, beleg_id)
    except service.BelegFehler as e:
        con.rollback()
        flash(str(e), "fehler")
        return redirect(url_for(".detail", beleg_id=beleg_id))
    con.commit()
    flash("Tags gespeichert.", "ok")
    return redirect(url_for(".detail", beleg_id=beleg_id))


@bp.route("/<int:beleg_id>/bearbeiten", methods=["GET", "POST"])
def bearbeiten(beleg_id: int):
    con = get_db()
    try:
        b = service.laden(con, beleg_id)
    except service.BelegFehler:
        abort(404)
    if b["status"] != "entwurf":
        flash("Festgeschriebene Belege können nicht mehr geändert werden. Bitte stornieren und neu erfassen.", "fehler")
        return redirect(url_for(".detail", beleg_id=beleg_id))
    if request.method == "GET":
        return _formular(service.fuer_formular(b), beleg_id=beleg_id)
    gewaehlt, neue = request.form.getlist("tags"), request.form.get("neue_tags", "")
    try:
        werte = service.formular_auswerten(request.form)
        service.aendern(con, _daten(), beleg_id, werte, _datei_aus_anfrage())
        _tags_speichern(con, beleg_id)
        if request.form.get("aktion") == "festschreiben":
            service.festschreiben(con, beleg_id)
    except service.ValidierungsFehler as e:
        return _formular({**e.werte, "datei_name": b["datei_name"]}, e.fehler, beleg_id=beleg_id, status=422,
                         gewaehlt=gewaehlt, neue_tags=neue)
    except service.BelegFehler as e:
        con.rollback()
        return _formular({**dict(request.form), "datei_name": b["datei_name"]}, {"datei": str(e)},
                         beleg_id=beleg_id, status=422, gewaehlt=gewaehlt, neue_tags=neue)
    con.commit()
    flash("Beleg gespeichert.", "ok")
    return redirect(url_for(".detail", beleg_id=beleg_id))


def _aktion(beleg_id: int, funktion, erfolg: str, *args):
    con = get_db()
    try:
        ergebnis = funktion(con, beleg_id, *args)
    except service.BelegFehler as e:
        con.rollback()
        flash(str(e), "fehler")
        return redirect(url_for(".detail", beleg_id=beleg_id))
    con.commit()
    flash(erfolg, "ok")
    return ergebnis


@bp.post("/<int:beleg_id>/festschreiben")
def festschreiben(beleg_id: int):
    r = _aktion(beleg_id, service.festschreiben,
                "Beleg festgeschrieben. Er kann jetzt nicht mehr geändert, nur noch storniert werden.")
    return r if isinstance(r, Response) else redirect(url_for(".detail", beleg_id=beleg_id))


@bp.post("/<int:beleg_id>/zahlung")
def zahlung(beleg_id: int):
    r = _aktion(beleg_id, service.zahlung_erfassen, "Zahlung erfasst.",
                (request.form.get("zahlungsdatum") or "").strip(), request.form.get("zahlungsart") or None)
    return r if isinstance(r, Response) else redirect(url_for(".detail", beleg_id=beleg_id))


@bp.post("/<int:beleg_id>/stornieren")
def stornieren(beleg_id: int):
    r = _aktion(beleg_id, service.stornieren, "Beleg storniert – eine Gegenbuchung wurde angelegt.",
                request.form.get("grund", ""))
    return r if isinstance(r, Response) else redirect(url_for(".detail", beleg_id=r))


@bp.post("/<int:beleg_id>/loeschen")
def loeschen(beleg_id: int):
    r = _aktion(beleg_id, service.loeschen, "Entwurf gelöscht.")
    return r if isinstance(r, Response) else redirect(url_for(".liste"))


@bp.get("/<int:beleg_id>/datei")
def datei(beleg_id: int):
    con = get_db()
    try:
        b = service.laden(con, beleg_id)
    except service.BelegFehler:
        abort(404)
    pfad = service.datei_pfad(_daten(), b)
    if pfad is None:
        abort(404)
    return send_file(pfad, download_name=b["datei_name"], as_attachment=request.args.get("download") == "1")


@bp.get("/export.csv")
def export_csv():
    flt = _filter()
    con = get_db()
    belege = service.liste(con, flt)
    text = service.csv_export(belege, tagmod.fuer_belege(con, [b["id"] for b in belege]))
    return Response(text, content_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename=belege_{flt.jahr or 'alle'}.csv"})


# --------------------------------------------------------------------------- Tags verwalten

@bp.get("/tags/")
def tags_liste():
    return render_template("belege/tags.html", alle_tags=tagmod.alle(get_db()), kategorien=kategorien.ALLE,
                           einnahmen=kategorien.EINNAHMEN, ausgaben=kategorien.AUSGABEN)


@bp.post("/tags/neu")
def tag_neu():
    con = get_db()
    try:
        tagmod.anlegen(con, request.form.get("name", ""), request.form.get("kategorie") or None)
    except tagmod.TagFehler as e:
        flash(str(e), "fehler")
        return redirect(url_for(".tags_liste"))
    con.commit()
    flash("Tag angelegt.", "ok")
    return redirect(url_for(".tags_liste"))


@bp.post("/tags/<int:tag_id>")
def tag_aendern(tag_id: int):
    con = get_db()
    try:
        tagmod.aendern(con, tag_id, request.form.get("name", ""), request.form.get("kategorie") or None,
                       request.form.get("aktiv") == "1")
    except tagmod.TagFehler as e:
        flash(str(e), "fehler")
        return redirect(url_for(".tags_liste"))
    con.commit()
    flash("Tag gespeichert.", "ok")
    return redirect(url_for(".tags_liste"))


# --------------------------------------------------------------------------- Auswertung nach Tags

def _auswertung() -> auswertung.Auswertung:
    con = get_db()
    try:
        jahr = int(request.args.get("jahr") or service.standard_jahr(con))
    except ValueError:
        jahr = int(service.standard_jahr(con))
    return auswertung.berechnen(con, jahr, art=request.args.get("art", "ausgabe"),
                                datum=request.args.get("datum", "beleg"),
                                nur_festgeschrieben=request.args.get("nur_fest") == "1")


@bp.get("/auswertung")
def auswertung_seite():
    rechnungen_abgleichen()
    return render_template("belege/auswertung.html", a=_auswertung(), jahre=service.jahre(get_db()),
                           ARTEN=auswertung.ARTEN, DATUMSARTEN=auswertung.DATUMSARTEN, OHNE_TAG=auswertung.OHNE_TAG)


@bp.get("/auswertung.csv")
def auswertung_csv():
    a = _auswertung()
    name = f"belege_nach_tags_{a.art}_{a.jahr}.csv"
    return Response(auswertung.csv_export(a), content_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename={name}"})
