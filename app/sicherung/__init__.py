"""Datensicherung: Knopf „Datensicherung“ oben rechts → Ziel wählen (beliebiges Laufwerk oder Download),
optional mit Passwort (AES-256). Jede Sicherung wird im Änderungsprotokoll vermerkt."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from flask import Blueprint, current_app, flash, redirect, render_template, request, send_file, url_for

from .. import db, protokoll
from . import service

bp = Blueprint("sicherung", __name__, url_prefix="/sicherung")

BEREICH = "sicherung"
ERINNERUNG_TAGE = 30


def letzte_sicherung(con) -> dict | None:
    z = con.execute("SELECT zeitpunkt, details, benutzer FROM aenderungsprotokoll WHERE bereich = ? "
                    "ORDER BY id DESC LIMIT 1", (BEREICH,)).fetchone()
    if z is None:
        return None
    d = json.loads(z["details"] or "{}")
    d.update(zeitpunkt=z["zeitpunkt"], benutzer=z["benutzer"])
    try:
        d["tage"] = (datetime.now() - datetime.fromisoformat(z["zeitpunkt"])).days
    except ValueError:
        d["tage"] = None
    return d


def _vermerken(con, ergebnis: service.Ergebnis, ziel: str) -> None:
    protokoll.schreiben(con, BEREICH, 0, "erstellt", {
        "hinweise": [f"{ergebnis.anzahl} Datei{'' if ergebnis.anzahl == 1 else 'en'}, {service.groesse_text(ergebnis.groesse)}"
                     + (", mit Passwort (AES-256)" if ergebnis.verschluesselt else "") + f" → {ziel}"],
        "ziel": ziel, "datei": ergebnis.pfad.name, "groesse": ergebnis.groesse, "anzahl": ergebnis.anzahl,
        "verschluesselt": ergebnis.verschluesselt})
    con.commit()


def _quelle():
    cfg = current_app.config
    return Path(cfg["DATENVERZEICHNIS"]), Path(cfg["DATENBANK"]), cfg.get("DB_PASSWORT")


def _laufwerke_erlaubt() -> bool:
    return current_app.config.get("SICHERUNG_LAUFWERKE", True)


def _seite(status: int = 200, fehler: str | None = None):
    con = db.get_db()
    letzte = letzte_sicherung(con)
    daten, datenbank, _ = _quelle()
    dateien = service.zu_sichern(daten, datenbank)
    umfang = sum(p.stat().st_size for p in dateien) + (datenbank.stat().st_size if datenbank.exists() else 0)
    vorschlag = {"laufwerk": "", "ordner": service.STANDARD_ORDNER, "pfad": ""}
    if letzte and letzte.get("ziel") and letzte["ziel"] != "Download":
        vorschlag["pfad"] = letzte["ziel"]
    vorschlag.update({k: v for k, v in request.form.items() if k in vorschlag})
    return render_template("sicherung/seite.html", letzte=letzte, anzahl=len(dateien) + 1, umfang=umfang,
                           laufwerke=service.laufwerke() if _laufwerke_erlaubt() else [],
                           laufwerke_erlaubt=_laufwerke_erlaubt(), vorschlag=vorschlag, fehler=fehler,
                           passwort_min=service.PASSWORT_MIN, groesse=service.groesse_text,
                           daten=daten), status


@bp.get("/")
def seite():
    return _seite()


def _sperre():
    from ..einstellungen import _SPERRE    # nie gleichzeitig mit dem Ver-/Entschlüsseln der Datenbank
    return _SPERRE


@bp.post("/erstellen")
def erstellen():
    if not _laufwerke_erlaubt():
        return redirect(url_for("sicherung.seite"))
    f = request.form
    try:
        passwort = service.passwort_pruefen(f.get("passwort", ""), f.get("wiederholung", ""))
        ziel = service.zielordner(f.get("laufwerk", ""), f.get("ordner", ""), f.get("pfad", ""))
        daten, datenbank, db_pw = _quelle()
        with _sperre():
            ergebnis = service.erstellen(daten, datenbank, db_pw, ziel, passwort)
    except service.SicherungsFehler as e:
        return _seite(400, str(e))
    _vermerken(db.get_db(), ergebnis, str(ziel))
    flash(f"Sicherung erstellt: {ergebnis.pfad} – {ergebnis.anzahl} Datei{'' if ergebnis.anzahl == 1 else 'en'}, "
          f"{service.groesse_text(ergebnis.groesse)}{', mit Passwort geschützt' if ergebnis.verschluesselt else ''}. "
          "Die Datei wurde vollständig zurückgelesen und geprüft.", "ok")
    return redirect(url_for("sicherung.seite"))


@bp.post("/download")
def download():
    f = request.form
    try:
        passwort = service.passwort_pruefen(f.get("passwort", ""), f.get("wiederholung", ""))
        daten, datenbank, db_pw = _quelle()
        tmp = Path(tempfile.mkdtemp(prefix="buchhaltung-sicherung-"))
        with _sperre():
            ergebnis = service.erstellen(daten, datenbank, db_pw, tmp, passwort)
    except service.SicherungsFehler as e:
        return _seite(400, str(e))
    _vermerken(db.get_db(), ergebnis, "Download")
    antwort = send_file(ergebnis.pfad, mimetype="application/zip", as_attachment=True,
                        download_name=ergebnis.pfad.name, max_age=0)

    def aufraeumen():
        for p in tmp.glob("*"):
            try:
                p.unlink()
            except OSError:
                pass
        try:
            os.rmdir(tmp)
        except OSError:
            pass

    antwort.call_on_close(aufraeumen)
    return antwort


@bp.app_context_processor
def _hinweis():
    """Für den Knopf oben rechts: Erinnerung, wenn die letzte Sicherung zu lange her ist."""
    app = current_app
    if app.config.get("GESPERRT"):
        return {}
    try:
        letzte = letzte_sicherung(db.get_db())
    except Exception:
        return {}
    return {"SICHERUNG_FAELLIG": letzte is None or (letzte.get("tage") or 0) >= ERINNERUNG_TAGE,
            "SICHERUNG_LETZTE": letzte}
