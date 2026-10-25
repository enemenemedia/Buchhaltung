"""Weboberfläche des Leistungsverzeichnisses."""

from datetime import datetime

from flask import (Blueprint, Response, abort, current_app, flash, redirect, render_template, request,
                   url_for)

from .. import zahlen
from ..db import get_db
from . import importer, service
from .importer import ImportFehler
from .service import LeistungsFehler, ValidierungsFehler

bp = Blueprint("leistungen", __name__, url_prefix="/leistungen")

MIME = {
    "csv": "text/csv; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


@bp.app_context_processor
def _hilfen():
    return {"EINHEITEN": service.EINHEITEN, "ARTEN": service.ARTEN, "UST_SAETZE": service.UST_SAETZE,
            "einheit_name": service.einheit_name, "gesamtpreis": service.gesamt}


@bp.app_template_filter("geld")
def _geld(wert):
    return zahlen.geld(wert)


@bp.app_template_filter("menge")
def _menge(wert):
    return zahlen.menge(wert)


def _filter() -> dict:
    status = request.args.get("status", "aktiv")
    return {
        "suche": request.args.get("q", "").strip(),
        "art": request.args.get("art", "") if request.args.get("art") in service.EINHEITEN else "",
        "kategorie": request.args.get("kategorie", "").strip(),
        "status": status if status in ("aktiv", "inaktiv", "alle") else "aktiv",
    }


def _laden_oder_404(leistung_id: int):
    try:
        return service.laden(get_db(), leistung_id)
    except LeistungsFehler:
        abort(404)


def _formular_eingaben() -> dict:
    """Formularwerte für die erneute Anzeige (Zahlen in deutscher Schreibweise lassen)."""
    return {f: request.form.get(f, "") for f in service.FELDER}


def _anzeige_werte(zeile) -> dict:
    d = dict(zeile)
    d["preis"] = zahlen.geld(d["preis"], mit_waehrung=False)
    d["anzahl"] = zahlen.menge(d["anzahl"])
    return d


# ---------------------------------------------------------------------------
# Liste, Anlegen, Bearbeiten
# ---------------------------------------------------------------------------

@bp.get("/")
def liste():
    f = _filter()
    con = get_db()
    leistungen = service.liste(con, f["suche"], f["art"], f["kategorie"], f["status"])
    return render_template("leistungen/liste.html", leistungen=leistungen, filter=f,
                           kategorien=service.kategorien(con))


@bp.route("/neu", methods=["GET", "POST"])
def neu():
    con = get_db()
    if request.method == "POST":
        try:
            leistung_id = service.anlegen(con, request.form)
        except ValidierungsFehler as e:
            flash(str(e), "fehler")
            return render_template("leistungen/formular.html", daten=_formular_eingaben(), fehler=e.fehler,
                                   neu=True, kategorien=service.kategorien(con),
                                   naechste=service.naechste_nummer(con)), 422
        flash("Leistung wurde angelegt.", "ok")
        if request.form.get("weiter"):
            return redirect(url_for("leistungen.neu"))
        return redirect(url_for("leistungen.bearbeiten", leistung_id=leistung_id))
    vorgabe = {"art": request.args.get("art", "zeit"), "anzahl": "1", "ust_satz": "19"}
    if vorgabe["art"] not in service.EINHEITEN:
        vorgabe["art"] = "zeit"
    return render_template("leistungen/formular.html", daten=vorgabe, fehler={}, neu=True,
                           kategorien=service.kategorien(con), naechste=service.naechste_nummer(con))


@bp.route("/<int:leistung_id>", methods=["GET", "POST"])
def bearbeiten(leistung_id: int):
    con = get_db()
    leistung = _laden_oder_404(leistung_id)
    if request.method == "POST":
        try:
            geaendert = service.aendern(con, leistung_id, request.form)
        except ValidierungsFehler as e:
            flash(str(e), "fehler")
            return render_template("leistungen/formular.html", daten=_formular_eingaben(), fehler=e.fehler,
                                   neu=False, leistung=leistung, kategorien=service.kategorien(con),
                                   verlauf=service.verlauf(con, leistung_id)), 422
        flash("Änderungen gespeichert." if geaendert else "Keine Änderungen.", "ok")
        return redirect(url_for("leistungen.bearbeiten", leistung_id=leistung_id))
    return render_template("leistungen/formular.html", daten=_anzeige_werte(leistung), fehler={}, neu=False,
                           leistung=leistung, kategorien=service.kategorien(con),
                           verlauf=service.verlauf(con, leistung_id))


@bp.post("/<int:leistung_id>/aktiv")
def aktiv(leistung_id: int):
    _laden_oder_404(leistung_id)
    an = request.form.get("aktiv") == "1"
    service.aktiv_setzen(get_db(), leistung_id, an)
    flash("Leistung ist wieder aktiv." if an else
          "Leistung deaktiviert. Sie wird bei neuen Angeboten und Rechnungen nicht mehr vorgeschlagen.", "ok")
    return redirect(url_for("leistungen.bearbeiten", leistung_id=leistung_id))


@bp.post("/<int:leistung_id>/loeschen")
def loeschen(leistung_id: int):
    _laden_oder_404(leistung_id)
    alt = service.loeschen(get_db(), leistung_id)
    flash(f"Leistung {alt['nummer']} „{alt['bezeichnung']}“ wurde gelöscht.", "ok")
    return redirect(url_for("leistungen.liste"))


# ---------------------------------------------------------------------------
# Export & Vorlage
# ---------------------------------------------------------------------------

def _datei(inhalt: bytes, name: str, format_: str) -> Response:
    return Response(inhalt, mimetype=MIME[format_],
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@bp.get("/export.<format_>")
def export(format_: str):
    if format_ not in MIME:
        abort(404)
    f = _filter()
    leistungen = service.liste(get_db(), f["suche"], f["art"], f["kategorie"], f["status"])
    return _datei(importer.export(leistungen, format_), f"leistungen_{datetime.now():%Y-%m-%d}.{format_}", format_)


@bp.get("/vorlage.<format_>")
def vorlage(format_: str):
    if format_ not in MIME:
        abort(404)
    return _datei(importer.vorlage(format_), f"leistungen_vorlage.{format_}", format_)


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

@bp.get("/import")
def import_start():
    return render_template("leistungen/import.html", vorschau=None)


@bp.post("/import")
def import_hochladen():
    datei = request.files.get("datei")
    if datei is None or not datei.filename:
        flash("Bitte eine CSV- oder Excel-Datei auswählen.", "fehler")
        return redirect(url_for("leistungen.import_start"))
    verzeichnis = current_app.config["DATENVERZEICHNIS"]
    try:
        rohdaten = importer.einlesen(datei.filename, datei.read())
    except ImportFehler as e:
        flash(str(e), "fehler")
        return redirect(url_for("leistungen.import_start"))
    token = importer.zwischenspeichern(verzeichnis, rohdaten)
    return render_template("leistungen/import.html", vorschau=importer.vorschau(get_db(), rohdaten), token=token)


@bp.post("/import/<token>")
def import_bestaetigen(token: str):
    verzeichnis = current_app.config["DATENVERZEICHNIS"]
    try:
        rohdaten = importer.zwischenspeicher_laden(verzeichnis, token)
    except ImportFehler as e:
        flash(str(e), "fehler")
        return redirect(url_for("leistungen.import_start"))
    if request.form.get("aktion") == "abbrechen":
        importer.zwischenspeicher_entfernen(verzeichnis, token)
        flash("Import abgebrochen.", "ok")
        return redirect(url_for("leistungen.liste"))

    erg = importer.uebernehmen(get_db(), rohdaten,
                               bestehende_aktualisieren=request.form.get("bestehende") == "aktualisieren")
    importer.zwischenspeicher_entfernen(verzeichnis, token)
    teile = [f"{erg.angelegt} neu angelegt", f"{erg.aktualisiert} aktualisiert"]
    if erg.unveraendert:
        teile.append(f"{erg.unveraendert} unverändert")
    if erg.uebersprungen:
        teile.append(f"{erg.uebersprungen} übersprungen")
    if erg.fehlerhaft:
        teile.append(f"{erg.fehlerhaft} fehlerhaft und nicht übernommen")
    flash(f"Import aus {rohdaten.dateiname} abgeschlossen: " + ", ".join(teile) + ".", "ok")
    return redirect(url_for("leistungen.liste", status="alle"))
