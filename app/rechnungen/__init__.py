"""Weboberfläche der Rechnungen."""

from datetime import date

from flask import (Blueprint, Response, abort, current_app, flash, redirect, render_template, request,
                   send_file, url_for)

from .. import positionen as pos
from ..db import get_db
from ..leistungen import service as leistungen
from ..stammdaten import service as stammdaten
from . import service
from .service import RechnungsFehler, ValidierungsFehler

bp = Blueprint("rechnungen", __name__, url_prefix="/rechnungen")


@bp.app_context_processor
def _hilfen():
    return {"RECHNUNG_STATUS": service.STATUS, "rechnung_status": service.anzeige_status}


def _verzeichnis():
    return current_app.config["DATENVERZEICHNIS"]


def _laden_oder_404(rechnung_id: int):
    try:
        return service.laden(get_db(), rechnung_id)
    except RechnungsFehler:
        abort(404)


def _editor(daten: dict, positionen: list[dict], fehler: dict, rechnung=None, status: int = 200):
    con = get_db()
    einstellungen = stammdaten.laden(con)
    return render_template(
        "rechnungen/formular.html", daten=daten, positionen=positionen, fehler=fehler, rechnung=rechnung,
        kunden=pos.kunden_auswahl(con), leistungsliste=pos.leistungen_auswahl(con), einheiten=leistungen.EINHEITEN,
        preisausweis="netto", kleinunternehmer=bool(einstellungen["kleinunternehmer"]),
    ), status


@bp.get("/")
def liste():
    suche = request.args.get("q", "").strip()
    status = request.args.get("status", "")
    if status not in ("", *service.STATUS):
        status = ""
    con = get_db()
    return render_template("rechnungen/liste.html", rechnungen=service.liste(con, suche, status), suche=suche,
                           status=status, kennzahlen=service.summen_offen(con))


@bp.route("/neu", methods=["GET", "POST"])
def neu():
    con = get_db()
    if request.method == "POST":
        try:
            rechnung_id = service.anlegen(con, _verzeichnis(), request.form)
        except ValidierungsFehler as e:
            flash(str(e), "fehler")
            return _editor(dict(request.form), pos.aus_formular(request.form), e.fehler, status=422)
        flash("Rechnung als Entwurf gespeichert.", "ok")
        return redirect(url_for("rechnungen.detail", rechnung_id=rechnung_id))
    daten = service.vorgaben(con, request.args.get("kunde", type=int))
    if request.args.get("neukunde"):
        daten["kunde_modus"] = "neu"
    return _editor(daten, [], {})


@bp.post("/aus-angebot/<int:angebot_id>")
def aus_angebot(angebot_id: int):
    try:
        rechnung_id = service.aus_angebot(get_db(), angebot_id)
    except RechnungsFehler as e:
        flash(str(e), "fehler")
        return redirect(url_for("angebote.detail", angebot_id=angebot_id))
    flash("Rechnungsentwurf aus dem Angebot erstellt. Bitte Leistungsdatum prüfen und abschließen.", "ok")
    return redirect(url_for("rechnungen.detail", rechnung_id=rechnung_id))


@bp.get("/<int:rechnung_id>")
def detail(rechnung_id: int):
    con = get_db()
    r = _laden_oder_404(rechnung_id)
    return render_template(
        "rechnungen/detail.html", r=r, positionen=service.positionen(con, rechnung_id),
        summen=service.summen(con, r), verlauf=service.verlauf(con, rechnung_id),
        pruefliste=service.pruefliste(con, r) if r["status"] == "entwurf" else [],
        dateien=service.dateien(_verzeichnis(), r), einheit_name=leistungen.einheit_name,
        heute=date.today().isoformat(),
    )


@bp.route("/<int:rechnung_id>/bearbeiten", methods=["GET", "POST"])
def bearbeiten(rechnung_id: int):
    con = get_db()
    r = _laden_oder_404(rechnung_id)
    if r["status"] != "entwurf":
        flash("Abgeschlossene Rechnungen können nicht mehr geändert werden.", "fehler")
        return redirect(url_for("rechnungen.detail", rechnung_id=rechnung_id))
    if request.method == "POST":
        try:
            geaendert = service.aendern(con, _verzeichnis(), rechnung_id, request.form)
        except ValidierungsFehler as e:
            flash(str(e), "fehler")
            return _editor(dict(request.form), pos.aus_formular(request.form), e.fehler, r, 422)
        except RechnungsFehler as e:
            flash(str(e), "fehler")
            return redirect(url_for("rechnungen.detail", rechnung_id=rechnung_id))
        flash("Änderungen gespeichert." if geaendert else "Keine Änderungen.", "ok")
        return redirect(url_for("rechnungen.detail", rechnung_id=rechnung_id))
    daten = {k: ("" if r[k] is None else str(r[k])) for k in (*service.KOPF_FELDER, "kunde_id", "ansprechpartner_id")}
    daten["kunde_modus"] = "bestand"
    return _editor(daten, pos.fuer_editor(service.positionen(con, rechnung_id)), {}, r)


@bp.post("/<int:rechnung_id>/abschliessen")
def abschliessen(rechnung_id: int):
    _laden_oder_404(rechnung_id)
    try:
        xml, _pdf = service.abschliessen(get_db(), _verzeichnis(), rechnung_id)
        flash(f"Rechnung abgeschlossen. XRechnung und PDF wurden als „{xml.stem}“ im Rechnungsordner abgelegt.", "ok")
    except RechnungsFehler as e:
        flash(str(e), "fehler")
    return redirect(url_for("rechnungen.detail", rechnung_id=rechnung_id))


@bp.post("/<int:rechnung_id>/bezahlt")
def bezahlt(rechnung_id: int):
    _laden_oder_404(rechnung_id)
    try:
        service.bezahlt_setzen(get_db(), rechnung_id, request.form.get("bezahlt_am", ""))
        flash("Zahlungseingang erfasst.", "ok")
    except RechnungsFehler as e:
        flash(str(e), "fehler")
    return redirect(url_for("rechnungen.detail", rechnung_id=rechnung_id))


@bp.post("/<int:rechnung_id>/zahlung-zuruecksetzen")
def zahlung_zuruecksetzen(rechnung_id: int):
    _laden_oder_404(rechnung_id)
    try:
        service.zahlung_zuruecksetzen(get_db(), rechnung_id)
        flash("Die Rechnung ist wieder offen.", "ok")
    except RechnungsFehler as e:
        flash(str(e), "fehler")
    return redirect(url_for("rechnungen.detail", rechnung_id=rechnung_id))


@bp.post("/<int:rechnung_id>/loeschen")
def loeschen(rechnung_id: int):
    _laden_oder_404(rechnung_id)
    try:
        service.loeschen(get_db(), rechnung_id)
    except RechnungsFehler as e:
        flash(str(e), "fehler")
        return redirect(url_for("rechnungen.detail", rechnung_id=rechnung_id))
    flash(f"Entwurf #{rechnung_id} wurde gelöscht.", "ok")
    return redirect(url_for("rechnungen.liste"))


@bp.get("/<int:rechnung_id>/pdf")
def pdf(rechnung_id: int):
    r = _laden_oder_404(rechnung_id)
    dateien = service.dateien(_verzeichnis(), r)
    if dateien:
        if not dateien["pdf"].is_file():
            abort(404)
        return send_file(dateien["pdf"], mimetype="application/pdf", download_name=dateien["pdf"].name, max_age=0)
    inhalt = service.vorschau_pdf(get_db(), _verzeichnis(), rechnung_id)
    return Response(inhalt, mimetype="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="Rechnungsentwurf_{rechnung_id}.pdf"'})


@bp.get("/<int:rechnung_id>/xml")
def xml(rechnung_id: int):
    r = _laden_oder_404(rechnung_id)
    dateien = service.dateien(_verzeichnis(), r)
    if not dateien or not dateien["xml"].is_file():
        abort(404)
    return send_file(dateien["xml"], mimetype="application/xml", as_attachment=True,
                     download_name=dateien["xml"].name, max_age=0)
