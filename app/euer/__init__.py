"""Modul EÜR: Einnahmen-Überschuss-Rechnung je Kalenderjahr aus den Belegen."""

from __future__ import annotations

from datetime import date

from flask import Blueprint, Response, render_template, request

from ..belege import rechnungen_abgleichen
from ..belege.service import standard_jahr
from ..db import get_db
from . import pdf, service

bp = Blueprint("euer", __name__, url_prefix="/euer")


def _jahr() -> int:
    try:
        return int(request.args.get("jahr") or standard_jahr(get_db()))
    except ValueError:
        return date.today().year


def _nur_fest() -> bool:
    """Standard: auch ohne Festschreibung auswerten (Entwürfe enthalten)."""
    return request.args.get("nur_fest") == "1"


@bp.get("/")
def seite():
    rechnungen_abgleichen()
    con = get_db()
    jahr = _jahr()
    return render_template("euer/seite.html", erg=service.berechnen(con, jahr, nur_festgeschrieben=_nur_fest()),
                           jahre=service.jahre(con))


@bp.get("/<int:jahr>.csv")
def csv(jahr: int):
    rechnungen_abgleichen()
    text = service.csv_export(service.berechnen(get_db(), jahr, nur_festgeschrieben=_nur_fest()))
    return Response(text, content_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename=EUER_{jahr}.csv"})


@bp.get("/<int:jahr>.pdf")
def als_pdf(jahr: int):
    rechnungen_abgleichen()
    con = get_db()
    s = con.execute("SELECT firma FROM stammdaten LIMIT 1").fetchone()
    daten = pdf.erzeugen(service.berechnen(con, jahr, nur_festgeschrieben=_nur_fest()), s["firma"] if s else "")
    return Response(daten, mimetype="application/pdf",
                    headers={"Content-Disposition": f"inline; filename=EUER_{jahr}.pdf"})
