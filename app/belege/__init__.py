"""Modul Belegerfassung: Einnahmen und Ausgaben mit Belegdatei, Festschreiben, Storno, Export."""

from __future__ import annotations

from datetime import date

from flask import (Blueprint, Response, abort, current_app, flash, redirect, render_template, request,
                   send_file, url_for)

from .. import zahlen
from ..db import get_db
from . import abgleich, kategorien, service

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


def _formular(beleg: dict, fehler=None, beleg_id=None, status=200):
    return render_template("belege/formular.html", beleg=beleg, fehler=fehler or {}, beleg_id=beleg_id,
                           einnahmen=kategorien.EINNAHMEN, ausgaben=kategorien.AUSGABEN,
                           zahlungsarten=service.ZAHLUNGSARTEN, ust_texte=service.UST_TEXT,
                           ohne_ust=sorted(kategorien.OHNE_UST)), status


def _filter() -> service.Filter:
    return service.Filter(jahr=request.args.get("jahr", str(date.today().year)), art=request.args.get("art", ""),
                          status=request.args.get("status", ""), suche=request.args.get("q", "").strip())


@bp.get("/")
def liste():
    rechnungen_abgleichen()
    con = get_db()
    flt = _filter()
    belege = service.liste(con, flt)
    return render_template("belege/liste.html", belege=belege, filter=flt, jahre=service.jahre(con),
                           kennzahlen=service.kennzahlen(belege, flt.jahr), STATUS=service.STATUS)


@bp.route("/neu", methods=["GET", "POST"])
def neu():
    if request.method == "GET":
        art = request.args.get("art", "ausgabe")
        art = art if art in ("einnahme", "ausgabe") else "ausgabe"
        return _formular({"art": art, "belegdatum": date.today().isoformat(), "ust_satz": "19",
                          "kategorie": kategorien.STANDARD[art]})
    con = get_db()
    try:
        werte = service.formular_auswerten(request.form)
        bid = service.anlegen(con, _daten(), werte, _datei_aus_anfrage(),
                              festschreiben_sofort=request.form.get("aktion") == "festschreiben")
    except service.ValidierungsFehler as e:
        return _formular(e.werte, e.fehler, status=422)
    except service.BelegFehler as e:
        con.rollback()
        werte = dict(request.form)
        return _formular(werte, {"datei": str(e)}, status=422)
    con.commit()
    _doppelt_warnen(con, bid)
    flash("Beleg gespeichert." if request.form.get("aktion") != "festschreiben"
          else "Beleg gespeichert und festgeschrieben.", "ok")
    if request.form.get("weiter"):
        return redirect(url_for(".neu", art=werte["art"]))
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
    return render_template("belege/detail.html", b=b, storno=storno, original=original, rechnung=rechnung,
                           protokoll=service.protokoll_lesen(con, beleg_id), STATUS=service.STATUS,
                           ust_texte=service.UST_TEXT, zahlungsarten=service.ZAHLUNGSARTEN)


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
    try:
        werte = service.formular_auswerten(request.form)
        service.aendern(con, _daten(), beleg_id, werte, _datei_aus_anfrage())
        if request.form.get("aktion") == "festschreiben":
            service.festschreiben(con, beleg_id)
    except service.ValidierungsFehler as e:
        return _formular({**e.werte, "datei_name": b["datei_name"]}, e.fehler, beleg_id=beleg_id, status=422)
    except service.BelegFehler as e:
        con.rollback()
        return _formular({**dict(request.form), "datei_name": b["datei_name"]}, {"datei": str(e)},
                         beleg_id=beleg_id, status=422)
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
    text = service.csv_export(service.liste(get_db(), flt))
    return Response(text, content_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename=belege_{flt.jahr or 'alle'}.csv"})
