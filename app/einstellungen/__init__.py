"""Einstellungen: Lizenz und Verschlüsselung der Datenbank (mit Entsperren/Sperren)."""

import threading
import time
from urllib.parse import urlparse

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for

from .. import aktive_lizenz, db, lizenz

bp = Blueprint("einstellungen", __name__, url_prefix="/einstellungen")

# Umschreiben der Datenbank und Entsperren nie gleichzeitig
_SPERRE = threading.Lock()


def _eigene_seite() -> None:
    """Schutz vor Formularen fremder Webseiten (der Server läuft lokal im Browser)."""
    herkunft = request.headers.get("Origin") or request.headers.get("Referer")
    if herkunft and urlparse(herkunft).netloc != request.host:
        abort(403)


def _pfad():
    return current_app.config["DATENBANK"]


def _seite(status: int = 200, **fehler):
    cfg = current_app.config
    return render_template(
        "einstellungen/seite.html", lizenz=aktive_lizenz(), gespeichert=cfg.get("LIZENZ"),
        lizenz_fehler=cfg.get("LIZENZ_FEHLER", ""), verschluesselt=cfg["DB_PASSWORT"] is not None,
        verfuegbar=db.VERSCHLUESSELUNG_VERFUEGBAR, passwort_min=db.PASSWORT_MIN, fehler=fehler,
        eingabe=request.form if request.method == "POST" else {},
    ), status


@bp.get("/")
def seite():
    return _seite()


# ---------------------------------------------------------------------------
# Lizenz
# ---------------------------------------------------------------------------

@bp.post("/lizenz")
def lizenz_speichern():
    _eigene_seite()
    try:
        l = lizenz.pruefen(request.form.get("lizenznehmer", ""), request.form.get("lizenznummer", ""))
    except lizenz.LizenzFehler as e:
        flash(str(e), "fehler")
        return _seite(422, lizenz=str(e))
    lizenz.speichern(current_app.config["DATENVERZEICHNIS"], l)
    current_app.config["LIZENZ"], current_app.config["LIZENZ_FEHLER"] = l, ""
    flash(f"Vielen Dank! Lizenziert für {l.lizenznehmer}. XRechnungen werden ab sofort erstellt.", "ok")
    return redirect(url_for("einstellungen.seite"))


# ---------------------------------------------------------------------------
# Verschlüsselung
# ---------------------------------------------------------------------------

@bp.post("/verschluesseln")
def verschluesseln():
    _eigene_seite()
    pw, pw2 = request.form.get("passwort", ""), request.form.get("passwort2", "")
    meldung = db.passwort_pruefen(pw, pw2)
    if meldung:
        return _seite(422, verschluesseln=meldung)
    if not request.form.get("verstanden"):
        return _seite(422, verschluesseln="Bitte bestätigen Sie, dass Sie das Passwort sicher notiert haben.")
    with _SPERRE:
        db.schliessen()
        try:
            db.verschluesseln(_pfad(), pw)
        except (db.VerschluesselungsFehler, db.PasswortFalsch) as e:
            return _seite(422, verschluesseln=str(e))
        current_app.config["DB_PASSWORT"] = pw
    current_app.logger.info("Datenbank verschlüsselt")
    flash("Die Datenbank ist jetzt verschlüsselt. Beim nächsten Start wird das Passwort abgefragt.", "ok")
    return redirect(url_for("einstellungen.seite") + "#verschluesselung")


@bp.post("/passwort-aendern")
def passwort_aendern():
    _eigene_seite()
    alt, neu, neu2 = (request.form.get(k, "") for k in ("alt", "passwort", "passwort2"))
    if alt != current_app.config["DB_PASSWORT"]:
        time.sleep(1)
        return _seite(422, passwort="Das bisherige Passwort ist falsch.")
    meldung = db.passwort_pruefen(neu, neu2)
    if meldung:
        return _seite(422, passwort=meldung)
    with _SPERRE:
        db.schliessen()
        try:
            db.passwort_aendern(_pfad(), alt, neu)
        except (db.VerschluesselungsFehler, db.PasswortFalsch) as e:
            return _seite(422, passwort=str(e))
        current_app.config["DB_PASSWORT"] = neu
    flash("Das Passwort wurde geändert.", "ok")
    return redirect(url_for("einstellungen.seite") + "#verschluesselung")


@bp.post("/entschluesseln")
def entschluesseln():
    _eigene_seite()
    pw = request.form.get("passwort", "")
    if pw != current_app.config["DB_PASSWORT"]:
        time.sleep(1)
        return _seite(422, entschluesseln="Das Passwort ist falsch.")
    with _SPERRE:
        db.schliessen()
        try:
            db.entschluesseln(_pfad(), pw)
        except (db.VerschluesselungsFehler, db.PasswortFalsch) as e:
            return _seite(422, entschluesseln=str(e))
        current_app.config["DB_PASSWORT"] = None
    flash("Die Verschlüsselung wurde aufgehoben. Die Datenbank ist nicht mehr durch ein Passwort geschützt.", "ok")
    return redirect(url_for("einstellungen.seite") + "#verschluesselung")


def _weiter() -> str:
    ziel = request.values.get("weiter", "/")
    # nur Pfade innerhalb der eigenen Anwendung
    return ziel if ziel.startswith("/") and not ziel.startswith("//") else "/"


@bp.route("/entsperren", methods=["GET", "POST"])
def entsperren():
    if not current_app.config["GESPERRT"]:
        return redirect(_weiter())
    fehler = ""
    if request.method == "POST":
        _eigene_seite()
        pw = request.form.get("passwort", "")
        with _SPERRE:
            try:
                db.initialisieren(_pfad(), pw)       # prüft das Passwort und ergänzt neue Spalten
            except db.PasswortFalsch:
                time.sleep(1)                          # verlangsamt das Durchprobieren
                fehler = "Das Passwort ist falsch."
            except db.VerschluesselungsFehler as e:
                fehler = str(e)
            else:
                current_app.config.update(DB_PASSWORT=pw, GESPERRT=False)
                return redirect(_weiter())
    return render_template("einstellungen/entsperren.html", fehler=fehler, weiter=_weiter()), 401 if fehler else 200


@bp.post("/sperren")
def sperren():
    _eigene_seite()
    if current_app.config["DB_PASSWORT"] is not None:
        db.schliessen()
        current_app.config.update(DB_PASSWORT=None, GESPERRT=True)
    return redirect(url_for("einstellungen.entsperren"))
