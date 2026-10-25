"""Weboberfläche der Kundenverwaltung."""

from datetime import date

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for

from ..db import get_db
from . import service
from .service import KundenFehler, ValidierungsFehler

bp = Blueprint("kunden", __name__, url_prefix="/kunden")


def _ap_aus_request() -> list[dict]:
    """Liest die Ansprechpartner-Zeilen (Felder ap-<n>-<feld>) aus dem Formular."""
    indizes = sorted({int(k.split("-")[1]) for k in request.form if k.startswith("ap-") and k.split("-")[1].isdigit()})
    haupt = request.form.get("hauptkontakt")
    zeilen = []
    for i in indizes:
        zeile = {f: request.form.get(f"ap-{i}-{f}", "") for f in (*service.ANSPRECHPARTNER_FELDER, "id")}
        zeile["ist_hauptkontakt"] = haupt == str(i)
        zeilen.append(zeile)
    return service.ansprechpartner_aus_formular(zeilen)


def _laden_oder_404(kunde_id: int):
    try:
        return service.laden(get_db(), kunde_id)
    except KundenFehler:
        abort(404)


@bp.get("/")
def liste():
    suche = request.args.get("q", "").strip()
    status = request.args.get("status", "aktiv")
    if status not in ("aktiv", "archiviert", "alle"):
        status = "aktiv"
    kunden = service.liste(get_db(), suche, status)
    return render_template("kunden/liste.html", kunden=kunden, suche=suche, status=status)


@bp.route("/neu", methods=["GET", "POST"])
def neu():
    fehler: dict = {}
    if request.method == "POST":
        ap = _ap_aus_request()
        try:
            kunde_id = service.anlegen(get_db(), current_app.config["DATENVERZEICHNIS"], request.form, ap)
        except ValidierungsFehler as e:
            fehler = e.fehler
            flash(str(e), "fehler")
            return render_template("kunden/formular.html", kunde=dict(request.form), ansprechpartner=ap,
                                   fehler=fehler, neu=True), 422
        flash("Kunde wurde angelegt.", "ok")
        return redirect(url_for("kunden.detail", kunde_id=kunde_id))
    return render_template("kunden/formular.html", kunde={"land": "DE"}, ansprechpartner=[],
                           fehler=fehler, neu=True)


@bp.get("/<int:kunde_id>")
def detail(kunde_id: int):
    con = get_db()
    kunde = _laden_oder_404(kunde_id)
    return render_template(
        "kunden/detail.html",
        kunde=kunde,
        ansprechpartner=service.ansprechpartner(con, kunde_id),
        protokoll=service.protokoll(con, kunde_id),
        loeschbar=service.ist_loeschbar(kunde),
        ordner=service.kundenordner(current_app.config["DATENVERZEICHNIS"], kunde["kundennummer"]),
        loeschbar_ab=date.fromisoformat(kunde["loeschbar_ab"]) if kunde["loeschbar_ab"] else None,
        angebote=_angebote(con, kunde_id),
        rechnungen=_rechnungen(con, kunde_id),
    )


def _rechnungen(con, kunde_id: int):
    from ..rechnungen import service as rechnungen
    return rechnungen.fuer_kunde(con, kunde_id)


def _angebote(con, kunde_id: int):
    from ..angebote import service as angebote  # spät importieren (gegenseitige Abhängigkeit)
    return angebote.fuer_kunde(con, kunde_id)


@bp.route("/<int:kunde_id>/bearbeiten", methods=["GET", "POST"])
def bearbeiten(kunde_id: int):
    con = get_db()
    kunde = _laden_oder_404(kunde_id)
    if kunde["archiviert_am"]:
        flash("Archivierte Kunden können nicht bearbeitet werden. Bitte zuerst reaktivieren.", "fehler")
        return redirect(url_for("kunden.detail", kunde_id=kunde_id))
    if request.method == "POST":
        ap = _ap_aus_request()
        try:
            geaendert = service.aendern(con, kunde_id, request.form, ap)
        except ValidierungsFehler as e:
            flash(str(e), "fehler")
            daten = dict(request.form, kundennummer=kunde["kundennummer"])
            return render_template("kunden/formular.html", kunde=daten, ansprechpartner=ap,
                                   fehler=e.fehler, neu=False, kunde_id=kunde_id), 422
        flash("Änderungen gespeichert." if geaendert else "Keine Änderungen.", "ok")
        return redirect(url_for("kunden.detail", kunde_id=kunde_id))
    return render_template("kunden/formular.html", kunde=dict(kunde),
                           ansprechpartner=[dict(a) for a in service.ansprechpartner(con, kunde_id)],
                           fehler={}, neu=False, kunde_id=kunde_id)


@bp.post("/<int:kunde_id>/archivieren")
def archivieren(kunde_id: int):
    _laden_oder_404(kunde_id)
    try:
        frist = service.archivieren(get_db(), kunde_id)
        flash(f"Kunde archiviert. Endgültiges Löschen ist ab dem {frist:%d.%m.%Y} möglich.", "ok")
    except KundenFehler as e:
        flash(str(e), "fehler")
    return redirect(url_for("kunden.detail", kunde_id=kunde_id))


@bp.post("/<int:kunde_id>/reaktivieren")
def reaktivieren(kunde_id: int):
    _laden_oder_404(kunde_id)
    try:
        service.reaktivieren(get_db(), kunde_id)
        flash("Kunde wurde reaktiviert.", "ok")
    except KundenFehler as e:
        flash(str(e), "fehler")
    return redirect(url_for("kunden.detail", kunde_id=kunde_id))


@bp.post("/<int:kunde_id>/loeschen")
def loeschen(kunde_id: int):
    kunde = _laden_oder_404(kunde_id)
    if request.form.get("bestaetigung", "").strip() != kunde["kundennummer"]:
        flash("Zum Löschen bitte die Kundennummer zur Bestätigung eingeben.", "fehler")
        return redirect(url_for("kunden.detail", kunde_id=kunde_id))
    try:
        service.loeschen(get_db(), current_app.config["DATENVERZEICHNIS"], kunde_id)
    except KundenFehler as e:
        flash(str(e), "fehler")
        return redirect(url_for("kunden.detail", kunde_id=kunde_id))
    flash(f"Kunde {kunde['kundennummer']} wurde endgültig gelöscht.", "ok")
    return redirect(url_for("kunden.liste", status="archiviert"))
