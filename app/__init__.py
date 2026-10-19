"""X-Rechnung für KMU – Flask-Anwendung."""

import os
import secrets
from pathlib import Path

from flask import Flask, redirect, url_for

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
    )

    db.initialisieren(app.config["DATENBANK"])
    app.teardown_appcontext(db.schliessen)

    from .kunden import bp as kunden_bp
    app.register_blueprint(kunden_bp)

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
        return redirect(url_for("kunden.liste"))

    return app
