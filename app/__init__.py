"""X-Rechnung für KMU – Flask-Anwendung."""

import os
import secrets
from pathlib import Path

from flask import Flask, flash, redirect, request, url_for

from . import db


def create_app(datenverzeichnis: str | Path | None = None) -> Flask:
    app = Flask(__name__)

    # Datenbank und alle Kundenordner liegen gemeinsam in einem Verzeichnis.
    daten = Path(datenverzeichnis or os.environ.get("XRECHNUNG_DATEN", "daten")).resolve()
    daten.mkdir(parents=True, exist_ok=True)
    schluessel_datei = daten / ".secret_key"
    if not schluessel_datei.exists():
        schluessel_datei.write_text(secrets.token_hex(32), encoding="utf-8")

    app.config.update(
        DATENVERZEICHNIS=daten,
        DATENBANK=daten / "xrechnung.db",
        SECRET_KEY=schluessel_datei.read_text(encoding="utf-8").strip(),
        # Obergrenze für Uploads (Logo max. 5 MB + Formularanteil)
        MAX_CONTENT_LENGTH=6 * 1024 * 1024,
    )

    db.initialisieren(app.config["DATENBANK"])
    app.teardown_appcontext(db.schliessen)

    from .angebote import bp as angebote_bp
    from .kunden import bp as kunden_bp
    from .leistungen import bp as leistungen_bp
    from .stammdaten import bp as stammdaten_bp
    app.register_blueprint(kunden_bp)
    app.register_blueprint(stammdaten_bp)
    app.register_blueprint(leistungen_bp)
    app.register_blueprint(angebote_bp)

    @app.errorhandler(413)
    def zu_gross(_e):
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

    @app.get("/")
    def start():
        # Beim ersten Start zuerst die eigenen Firmendaten erfassen.
        con = db.get_db()
        if not con.execute("SELECT geaendert_am FROM stammdaten WHERE id = 1").fetchone()["geaendert_am"]:
            return redirect(url_for("stammdaten.seite"))
        return redirect(url_for("kunden.liste"))

    return app
