"""Weboberfläche der Angebote."""

from datetime import date

from flask import (Blueprint, Response, abort, current_app, flash, redirect, render_template, request,
                   send_file, url_for)

from .. import positionen as pos
from ..db import get_db
from ..leistungen import service as leistungen
from ..stammdaten import service as stammdaten
from . import service
from .service import AngebotsFehler, ValidierungsFehler

bp = Blueprint("angebote", __name__, url_prefix="/angebote")


@bp.app_context_processor
def _hilfen():
    return {"ANGEBOT_STATUS": service.STATUS, "angebot_status": service.anzeige_status,
            "angebot_tage": service.tage_gueltig}


def _laden_oder_404(angebot_id: int):
    try:
        return service.laden(get_db(), angebot_id)
    except AngebotsFehler:
        abort(404)


def _editor(daten: dict, positionen: list[dict], fehler: dict, angebot=None, status: int = 200):
    con = get_db()
    einstellungen = stammdaten.laden(con)
    return render_template(
        "angebote/formular.html",
        daten=daten, positionen=positionen, fehler=fehler, angebot=angebot,
        kunden=service.kunden_auswahl(con), leistungsliste=service.leistungen_auswahl(con),
        einheiten=leistungen.EINHEITEN, preisausweis=service.preisausweis(einstellungen),
        kleinunternehmer=bool(einstellungen["kleinunternehmer"]),
    ), status


def _positionen_fuer_editor(form) -> list[dict]:
    """Formularzeilen nach einem Fehler unverändert wieder anzeigen."""
    return service.positionen_aus_formular(form)


@bp.get("/")
def liste():
    suche = request.args.get("q", "").strip()
    status = request.args.get("status", "")
    if status not in ("", *service.STATUS):
        status = ""
    return render_template("angebote/liste.html", angebote=service.liste(get_db(), suche, status),
                           suche=suche, status=status)


@bp.route("/neu", methods=["GET", "POST"])
def neu():
    con = get_db()
    if request.method == "POST":
        try:
            angebot_id = service.anlegen(con, request.form)
        except ValidierungsFehler as e:
            flash(str(e), "fehler")
            return _editor(dict(request.form), _positionen_fuer_editor(request.form), e.fehler, status=422)
        flash("Angebot als Entwurf gespeichert.", "ok")
        return redirect(url_for("angebote.detail", angebot_id=angebot_id))
    kunde_id = request.args.get("kunde", type=int)
    return _editor(service.vorgaben(con, kunde_id), [], {})


@bp.get("/<int:angebot_id>")
def detail(angebot_id: int):
    con = get_db()
    a = _laden_oder_404(angebot_id)
    verzeichnis = current_app.config["DATENVERZEICHNIS"]
    return render_template(
        "angebote/detail.html", a=a, positionen=service.positionen(con, angebot_id),
        summen=service.summen(con, a), verlauf=service.verlauf(con, angebot_id),
        pdf_pfad=service.pdf_pfad(verzeichnis, a), einheit_name=leistungen.einheit_name,
        heute=date.today(),
        rechnungen=_rechnungen(con, angebot_id),
    )


def _rechnungen(con, angebot_id: int):
    from ..rechnungen import service as rechnungen
    return rechnungen.fuer_angebot(con, angebot_id)


@bp.route("/<int:angebot_id>/bearbeiten", methods=["GET", "POST"])
def bearbeiten(angebot_id: int):
    con = get_db()
    a = _laden_oder_404(angebot_id)
    if a["status"] != "entwurf":
        flash("Abgeschlossene Angebote können nicht mehr geändert werden. Legen Sie bei Bedarf eine Kopie an.", "fehler")
        return redirect(url_for("angebote.detail", angebot_id=angebot_id))
    if request.method == "POST":
        try:
            geaendert = service.aendern(con, angebot_id, request.form)
        except ValidierungsFehler as e:
            flash(str(e), "fehler")
            return _editor(dict(request.form), _positionen_fuer_editor(request.form), e.fehler, a, 422)
        except AngebotsFehler as e:
            flash(str(e), "fehler")
            return redirect(url_for("angebote.detail", angebot_id=angebot_id))
        flash("Änderungen gespeichert." if geaendert else "Keine Änderungen.", "ok")
        return redirect(url_for("angebote.detail", angebot_id=angebot_id))
    daten = {k: ("" if a[k] is None else str(a[k]))
             for k in ("kunde_id", "ansprechpartner_id", "datum", "gueltig_bis", "betreff", "anmerkungen")}
    positionen = pos.fuer_editor(service.positionen(con, angebot_id))
    return _editor(daten, positionen, {}, a)


@bp.post("/<int:angebot_id>/abschliessen")
def abschliessen(angebot_id: int):
    _laden_oder_404(angebot_id)
    try:
        pfad = service.abschliessen(get_db(), current_app.config["DATENVERZEICHNIS"], angebot_id)
        flash(f"Angebot abgeschlossen. Das PDF wurde im Kundenordner abgelegt ({pfad.name}).", "ok")
    except AngebotsFehler as e:
        flash(str(e), "fehler")
    return redirect(url_for("angebote.detail", angebot_id=angebot_id))


@bp.post("/<int:angebot_id>/status")
def status(angebot_id: int):
    _laden_oder_404(angebot_id)
    neu = request.form.get("status", "")
    try:
        service.status_setzen(get_db(), angebot_id, neu)
        flash(f"Status auf „{service.STATUS[neu]}“ gesetzt.", "ok")
    except AngebotsFehler as e:
        flash(str(e), "fehler")
    return redirect(url_for("angebote.detail", angebot_id=angebot_id))


@bp.post("/<int:angebot_id>/kopie")
def kopie(angebot_id: int):
    a = _laden_oder_404(angebot_id)
    try:
        neu_id = service.duplizieren(get_db(), angebot_id)
    except AngebotsFehler as e:
        flash(str(e), "fehler")
        return redirect(url_for("angebote.detail", angebot_id=angebot_id))
    flash(f"Kopie von {a['nummer']} als neuer Entwurf angelegt.", "ok")
    return redirect(url_for("angebote.bearbeiten", angebot_id=neu_id))


@bp.post("/<int:angebot_id>/loeschen")
def loeschen(angebot_id: int):
    _laden_oder_404(angebot_id)
    try:
        a = service.loeschen(get_db(), angebot_id)
    except AngebotsFehler as e:
        flash(str(e), "fehler")
        return redirect(url_for("angebote.detail", angebot_id=angebot_id))
    flash(f"Entwurf {a['nummer']} wurde gelöscht.", "ok")
    return redirect(url_for("angebote.liste"))


@bp.get("/<int:angebot_id>/pdf")
def pdf(angebot_id: int):
    con = get_db()
    a = _laden_oder_404(angebot_id)
    verzeichnis = current_app.config["DATENVERZEICHNIS"]
    pfad = service.pdf_pfad(verzeichnis, a)
    if pfad is not None:
        if not pfad.is_file():
            abort(404)
        return send_file(pfad, mimetype="application/pdf", download_name=pfad.name, max_age=0)
    # Entwurf: Vorschau mit Wasserzeichen, wird nicht gespeichert
    from . import dokument
    inhalt = dokument.erzeugen(a, service.positionen(con, angebot_id), stammdaten.laden(con), verzeichnis)
    return Response(inhalt, mimetype="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{a["nummer"]}_Entwurf.pdf"'})
