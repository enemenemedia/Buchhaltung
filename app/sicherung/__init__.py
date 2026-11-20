"""Datensicherung und Wiederherstellung: Knopf „Sichern“ oben rechts → Ziel wählen (beliebiges Laufwerk
oder Download), optional mit Passwort (AES-256); „Wiederherstellen“ spielt eine Sicherung zurück
(aus den bisherigen Zielordnern, von einem Pfad oder hochgeladen). Beides steht im Änderungsprotokoll."""

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
                    "AND aktion = 'erstellt' ORDER BY id DESC LIMIT 1", (BEREICH,)).fetchone()
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


def _zielordner_bisher(con) -> list[str]:
    ziele = [json.loads(z[0] or "{}").get("ziel") for z in con.execute(
        "SELECT details FROM aenderungsprotokoll WHERE bereich = ? AND aktion = 'erstellt' "
        "ORDER BY id DESC LIMIT 30", (BEREICH,))]
    return [z for z in dict.fromkeys(ziele) if z and z != "Download"]


def _seite(status: int = 200, fehler: str | None = None, fehler_wiederherstellen: str | None = None):
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
                           daten=daten, fehler_wiederherstellen=fehler_wiederherstellen,
                           gefunden=service.gefundene(_zielordner_bisher(con)) if _laufwerke_erlaubt() else [],
                           wiederhergestellt=con.execute(
                               "SELECT zeitpunkt, details, benutzer FROM aenderungsprotokoll WHERE bereich = ? AND "
                               "aktion = 'wiederhergestellt' ORDER BY id DESC LIMIT 1", (BEREICH,)).fetchone()), status


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


# ---------------------------------------------------------------------------
# Wiederherstellen
# ---------------------------------------------------------------------------

@bp.before_app_request
def _wartung():
    """Während eine Sicherung zurückgespielt wird, warten alle anderen Anfragen (Netzwerkversion)."""
    if current_app.config.get("WIEDERHERSTELLUNG_LAEUFT") and request.endpoint != "static":
        return ("Die Daten werden gerade aus einer Sicherung wiederhergestellt. Bitte in einer Minute neu laden.",
                503, {"Retry-After": "30", "Content-Type": "text/plain; charset=utf-8"})
    return None


@bp.post("/wiederherstellen")
def wiederherstellen():
    f = request.form
    cfg = current_app.config
    daten, datenbank, _ = _quelle()
    if f.get("bestaetigt") != "1":
        return _seite(400, fehler_wiederherstellen="Bitte bestätigen, dass der jetzige Stand ersetzt werden soll.")
    hochgeladen = request.files.get("datei")
    upload = daten / service.UPLOAD
    if hochgeladen and hochgeladen.filename:
        hochgeladen.save(upload)
        quelle = upload
    elif _laufwerke_erlaubt() and (f.get("gefunden") or f.get("pfad_zip", "").strip()):
        quelle = Path((f.get("pfad_zip", "").strip().strip('"')) or f["gefunden"])
    else:
        return _seite(400, fehler_wiederherstellen="Bitte eine Sicherung auswählen oder hochladen.")

    passwort = f.get("passwort_zip", "") or None
    benutzer = protokoll.angemeldeter_benutzer()
    try:
        with _sperre():
            db.schliessen()                             # eigene Verbindung zu, andere Anfragen warten
            cfg["WIEDERHERSTELLUNG_LAEUFT"] = True
            try:
                manifest = service.wiederherstellen(daten, datenbank, quelle, passwort)
            finally:
                cfg["WIEDERHERSTELLUNG_LAEUFT"] = False
    except service.SicherungsFehler as e:
        upload.unlink(missing_ok=True)
        return _seite(400, fehler_wiederherstellen=str(e))
    upload.unlink(missing_ok=True)

    try:
        erstellt = datetime.fromisoformat(manifest.get("erstellt", "")).strftime("%d.%m.%Y %H:%M")
    except ValueError:
        erstellt = manifest.get("erstellt", "?")
    anzahl = manifest.get("anzahl", 0)
    gesperrt = db.ist_verschluesselt(datenbank)
    cfg.update(DB_PASSWORT=None, GESPERRT=gesperrt)
    text = (f"Wiederhergestellt: Stand vom {erstellt} ({anzahl} Datei{'' if anzahl == 1 else 'en'}, "
            f"Version {manifest.get('version')}).")
    if manifest.get("vorher"):
        text += f" Der bisherige Stand wurde aufgehoben: {manifest['vorher']}"
    if not gesperrt:
        con = db.verbinden(datenbank)
        try:
            con.execute(
                "INSERT INTO aenderungsprotokoll (zeitpunkt, bereich, objekt_id, aktion, details, benutzer) "
                "VALUES (?, ?, 0, 'wiederhergestellt', ?, ?)",
                (protokoll.jetzt(), BEREICH, json.dumps({
                    "hinweise": [f"Sicherung vom {erstellt} ({quelle.name if quelle != upload else 'hochgeladen'})"],
                    "quelle": str(quelle) if quelle != upload else "hochgeladen", "stand": manifest.get("erstellt"),
                    "vorher": manifest.get("vorher")}, ensure_ascii=False), benutzer))
            con.commit()
        finally:
            con.close()
        flash(text, "ok")
        return redirect(url_for("sicherung.seite"))
    flash(text + " Die wiederhergestellte Datenbank ist verschlüsselt – bitte mit dem Datenbank-Passwort "
          "entsperren, das zum Zeitpunkt der Sicherung galt.", "ok")
    return redirect(url_for("einstellungen.entsperren"))
