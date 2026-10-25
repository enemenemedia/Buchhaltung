"""Weboberfläche der Firmenstammdaten."""

from flask import (Blueprint, abort, current_app, flash, redirect, render_template, request,
                   send_file, url_for)

from ..db import get_db
from . import service
from .service import StammdatenFehler, ValidierungsFehler

bp = Blueprint("stammdaten", __name__, url_prefix="/stammdaten")


def _seite(daten: dict, fehler: dict, status: int = 200):
    verzeichnis = current_app.config["DATENVERZEICHNIS"]
    con = get_db()
    gespeichert = service.laden(con)
    return render_template(
        "stammdaten/seite.html",
        daten=daten,
        fehler=fehler,
        gespeichert=gespeichert,
        fehlend=service.fehlende_pflichtangaben(gespeichert),
        logo=service.logo_vorhanden(verzeichnis),
        logo_version=(gespeichert["logo_aktualisiert_am"] or "").replace(" ", "T"),
        verlauf=service.verlauf(con),
        iban_formatiert=service.iban_formatiert,
    ), status


@bp.get("/")
def seite():
    return _seite(dict(service.laden(get_db())), {})


@bp.post("/")
def speichern():
    try:
        geaendert = service.speichern(get_db(), request.form)
    except ValidierungsFehler as e:
        flash(str(e), "fehler")
        daten = service.aus_formular(request.form)
        return _seite(daten, e.fehler, 422)
    flash("Stammdaten gespeichert." if geaendert else "Keine Änderungen.", "ok")
    return redirect(url_for("stammdaten.seite"))


@bp.post("/logo")
def logo_hochladen():
    datei = request.files.get("logo")
    if datei is None or not datei.filename:
        flash("Bitte eine Bilddatei auswählen.", "fehler")
        return redirect(url_for("stammdaten.seite") + "#logo")
    try:
        breite, hoehe = service.logo_speichern(
            get_db(), current_app.config["DATENVERZEICHNIS"], datei.read(), datei.filename)
        flash(f"Logo gespeichert ({breite} × {hoehe} px).", "ok")
    except StammdatenFehler as e:
        flash(str(e), "fehler")
    return redirect(url_for("stammdaten.seite") + "#logo")


@bp.post("/logo/entfernen")
def logo_entfernen():
    try:
        service.logo_entfernen(get_db(), current_app.config["DATENVERZEICHNIS"])
        flash("Logo entfernt. Die bisherige Datei wurde archiviert.", "ok")
    except StammdatenFehler as e:
        flash(str(e), "fehler")
    return redirect(url_for("stammdaten.seite") + "#logo")


@bp.get("/logo.png")
def logo_datei():
    pfad = service.logo_pfad(current_app.config["DATENVERZEICHNIS"])
    if not pfad.is_file():
        abort(404)
    antwort = send_file(pfad, mimetype="image/png", max_age=0)
    antwort.headers["X-Content-Type-Options"] = "nosniff"
    return antwort
