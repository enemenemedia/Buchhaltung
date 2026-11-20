"""Buchhaltung für KMU – Flask-Anwendung (Kunden, Angebote, Rechnungen mit XRechnung, Belegerfassung, EÜR)."""

__version__ = "1.4.0"
PROGRAMM_NAME = "Buchhaltung für KMU"

import os
import secrets
from pathlib import Path

from urllib.parse import urlparse

from flask import Flask, abort, flash, jsonify, redirect, request, url_for

from . import db, lizenz


APP_KENNUNG = "buchhaltung-kmu"


def _schalter(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "ja", "yes", "on")


def create_app(datenverzeichnis: str | Path | None = None, *, beenden=None, netzwerk: bool | None = None,
               einstellungen: dict | None = None) -> Flask:
    """beenden: optionale Funktion, die der Windows-Starter übergibt. Ist sie gesetzt,
    zeigt die Oberfläche einen Knopf „Beenden“, der den lokalen Server stoppt.
    netzwerk: Netzwerkversion mit Anmeldung, Benutzern und Rollen (Standard: Umgebungsvariable
    BUCHHALTUNG_NETZWERK=1). einstellungen: zusätzliche Flask-Konfiguration (z. B. für Tests)."""
    app = Flask(__name__)
    if netzwerk is None:
        netzwerk = _schalter("BUCHHALTUNG_NETZWERK")

    # Datenbank und alle Kundenordner liegen gemeinsam in einem Verzeichnis.
    daten = Path(datenverzeichnis or os.environ.get("BUCHHALTUNG_DATEN") or os.environ.get("XRECHNUNG_DATEN")
                 or "daten").resolve()
    daten.mkdir(parents=True, exist_ok=True)
    # Sitzungsschlüssel; die Windows-Netzwerkversion legt ihn lokal auf dem Server-PC ab
    # (SCHLUESSEL_ORDNER) statt auf dem Netzlaufwerk, das auch andere lesen können.
    schluessel_ordner = Path((einstellungen or {}).get("SCHLUESSEL_ORDNER") or daten)
    schluessel_ordner.mkdir(parents=True, exist_ok=True)
    schluessel_datei = schluessel_ordner / ".secret_key"
    if not schluessel_datei.exists():
        schluessel_datei.write_text(secrets.token_hex(32), encoding="utf-8")

    app.config.update(
        DATENVERZEICHNIS=daten,
        DATENBANK=daten / "xrechnung.db",
        SECRET_KEY=schluessel_datei.read_text(encoding="utf-8").strip(),
        # Obergrenze für Uploads (Logo max. 5 MB + Formularanteil); Belege: siehe _upload_grenze
        MAX_CONTENT_LENGTH=6 * 1024 * 1024,
        NETZWERK=False,
    )
    app.config.update(einstellungen or {})

    # Verschlüsselte Datenbank: bis zur Eingabe des Passworts gesperrt (Passwort nur im Speicher)
    app.config["DB_PASSWORT"] = None
    app.config["GESPERRT"] = db.ist_verschluesselt(app.config["DATENBANK"])
    if not app.config["GESPERRT"]:
        db.initialisieren(app.config["DATENBANK"])
    app.teardown_appcontext(db.schliessen)

    # Lizenz aus dem Datenordner; ohne gültige Lizenz läuft das Programm als Testversion
    app.config["LIZENZ"], app.config["LIZENZ_FEHLER"] = lizenz.laden(daten)

    from .angebote import bp as angebote_bp
    from .belege import bp as belege_bp
    from .euer import bp as euer_bp
    from .einstellungen import bp as einstellungen_bp
    from .kunden import bp as kunden_bp
    from .leistungen import bp as leistungen_bp
    from .rechnungen import bp as rechnungen_bp
    from .sicherung import bp as sicherung_bp
    from .stammdaten import bp as stammdaten_bp
    app.register_blueprint(kunden_bp)
    app.register_blueprint(stammdaten_bp)
    app.register_blueprint(sicherung_bp)
    app.register_blueprint(leistungen_bp)
    app.register_blueprint(angebote_bp)
    app.register_blueprint(rechnungen_bp)
    app.register_blueprint(belege_bp)
    app.register_blueprint(euer_bp)
    app.register_blueprint(einstellungen_bp)

    # Netzwerkversion: nur lokales Netz, Anmeldung, Rollen (die Prüfungen laufen vor allen anderen)
    if netzwerk:
        from . import netzwerk as netz
        netz.einrichten(app)

    # Solange die Datenbank gesperrt ist, führt jede Seite zur Passworteingabe
    FREI_GESPERRT = {"static", "kennung", "system_beenden", "einstellungen.entsperren", "konto.stopp"}

    @app.before_request
    def _sperre():
        if app.config["GESPERRT"] and request.endpoint not in FREI_GESPERRT:
            weiter = request.full_path if request.method == "GET" else "/"
            return redirect(url_for("einstellungen.entsperren", weiter=weiter.rstrip("?")))
        return None

    @app.before_request
    def _upload_grenze():
        # Belegdateien (Scans, Fotos) dürfen bis 20 MB groß sein
        if request.path.startswith("/belege"):
            request.max_content_length = 21 * 1024 * 1024

    @app.before_request
    def _fremde_formulare_abweisen():
        """Schutz vor Formularen fremder Webseiten (CSRF): Browser senden bei POST die Herkunft mit.
        Stimmt sie nicht mit diesem Programm überein, wird die Anfrage abgewiesen."""
        if request.method != "POST":
            return None
        herkunft = request.headers.get("Origin") or request.headers.get("Referer")
        if herkunft and herkunft != "null" and urlparse(herkunft).netloc != request.host:
            abort(403)
        if herkunft == "null":
            abort(403)
        return None

    @app.errorhandler(413)
    def zu_gross(_e):
        if request.path.startswith("/belege"):
            flash("Die Datei ist zu groß (höchstens 20 MB).", "fehler")
            return redirect(url_for("belege.liste"))
        flash("Die Datei ist zu groß (höchstens 5 MB).", "fehler")
        if request.path.startswith("/leistungen"):
            return redirect(url_for("leistungen.import_start"))
        return redirect(url_for("stammdaten.seite") + "#logo")

    @app.template_filter("datum")
    def datum(wert: str | None, mit_zeit: bool = False) -> str:
        if not wert:
            return ""
        teile = str(wert).split(" ")
        j, m, t = teile[0].split("-")
        text = f"{t}.{m}.{j}"
        if mit_zeit and len(teile) > 1:
            text += " " + teile[1][:5]
        return text

    @app.context_processor
    def _programm():
        return {"PROGRAMM_VERSION": __version__, "PROGRAMM_NAME": PROGRAMM_NAME, "DESKTOP": beenden is not None,
                "DATENORDNER": str(app.config["DATENVERZEICHNIS"]), "LIZENZ": aktive_lizenz(app),
                "LIZENZPFLICHT": lizenz.LIZENZPFLICHT, "XRECHNUNG_ERLAUBT": xrechnung_erlaubt(app),
                "VERSCHLUESSELT": app.config["DB_PASSWORT"] is not None, "GESPERRT": app.config["GESPERRT"],
                "NETZWERK": app.config["NETZWERK"]}

    @app.get("/system/kennung")
    def kennung():
        # Der Windows-Starter erkennt daran eine bereits laufende Instanz.
        if app.config["NETZWERK"]:      # im Netz den Speicherort nicht verraten
            return jsonify(app=APP_KENNUNG, version=__version__, netzwerk=True)
        return jsonify(app=APP_KENNUNG, version=__version__, daten=str(app.config["DATENVERZEICHNIS"]))

    @app.post("/system/beenden")
    def system_beenden():
        if beenden is None:
            abort(404)
        # Nur Anfragen aus der eigenen Oberfläche annehmen (Schutz vor fremden Webseiten)
        herkunft = request.headers.get("Origin") or request.headers.get("Referer") or ""
        if urlparse(herkunft).netloc != request.host:
            abort(403)
        beenden()
        return ("<!doctype html><meta charset='utf-8'><title>Beendet</title>"
                "<body style='font-family:system-ui;padding:3rem;color:#1d2420'>"
                f"<h1>{PROGRAMM_NAME} wurde beendet.</h1>"
                "<p>Sie können dieses Browserfenster schließen.</p>")

    @app.get("/")
    def start():
        # Beim ersten Start zuerst die eigenen Firmendaten erfassen.
        con = db.get_db()
        if not con.execute("SELECT geaendert_am FROM stammdaten WHERE id = 1").fetchone()["geaendert_am"]:
            return redirect(url_for("stammdaten.seite"))
        return redirect(url_for("kunden.liste"))

    return app


def xrechnung_erlaubt(app: Flask | None = None) -> bool:
    """Darf die XRechnung (XML) erstellt werden? Ohne Lizenzpflicht immer."""
    return not lizenz.LIZENZPFLICHT or aktive_lizenz(app) is not None


def aktive_lizenz(app: Flask | None = None) -> "lizenz.Lizenz | None":
    """Gültige Lizenz oder None (= Testversion: XRechnung wird nicht erstellt)."""
    from flask import current_app

    l = (app or current_app).config.get("LIZENZ")
    return l if l is not None and l.gueltig() else None
