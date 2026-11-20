"""Netzwerkversion: Anmeldung, Benutzer mit Rollen, Zugriff nur aus dem lokalen Netz.

Wird von create_app(..., netzwerk=True) bzw. mit BUCHHALTUNG_NETZWERK=1 eingeschaltet.
Die Einzelplatzversion bleibt davon unberührt.
"""

from __future__ import annotations

import ipaddress
import os
import secrets
import sys
from datetime import timedelta
from pathlib import Path

from flask import (Blueprint, Flask, abort, current_app, flash, g, redirect, render_template, request, send_file,
                   session, url_for)

from .. import PROGRAMM_NAME, db
from . import benutzer as bn

bp = Blueprint("konto", __name__)

# Ohne Anmeldung erreichbar
FREI = {"static", "kennung", "einstellungen.entsperren", "konto.anmelden", "konto.einrichtung", "konto.zertifikat",
        "konto.stopp"}
# Für jeden angemeldeten Benutzer, auch „Nur lesen“
EIGENES_KONTO = {"konto.abmelden", "konto.passwort"}
# Nur Administratoren (auch zum Ansehen)
NUR_ADMIN = ("/benutzer", "/einstellungen", "/sicherung")
# Nur Administratoren dürfen hier ändern; ansehen dürfen alle
AENDERN_NUR_ADMIN = ("/stammdaten",)
EINRICHTUNGSCODE_DATEI = "einrichtungscode.txt"


# ---------------------------------------------------------------------------
# Regeln
# ---------------------------------------------------------------------------

def _unter(pfad: str, praefixe: tuple[str, ...]) -> bool:
    return any(pfad == p or pfad.startswith(p + "/") for p in praefixe)


def erlaubt(rolle: str, methode: str, endpoint: str | None, pfad: str) -> bool:
    if endpoint in EIGENES_KONTO:
        return True
    if rolle == "admin":
        return True
    if _unter(pfad, NUR_ADMIN):
        return False
    if methode in ("GET", "HEAD", "OPTIONS"):
        return True
    if rolle == "lesen":
        return False
    return not _unter(pfad, AENDERN_NUR_ADMIN)


def netze_aus_text(text: str | None) -> list | None:
    """„192.168.1.0/24, 10.0.0.0/8“ → Netzliste; leer → None (= alle privaten Netze)."""
    if not text or not text.strip():
        return None
    return [ipaddress.ip_network(t.strip(), strict=False) for t in text.split(",") if t.strip()]


def im_lokalen_netz(adresse: str | None, netze: list | None) -> bool:
    try:
        ip = ipaddress.ip_address((adresse or "").split("%")[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip.is_loopback:
        return True
    if netze is None:
        # private Bereiche (192.168.x, 10.x, 172.16–31.x, fd00::/8) und link-local
        return (ip.is_private or ip.is_link_local) and not ip.is_unspecified
    return any(ip.version == n.version and ip in n for n in netze)


# ---------------------------------------------------------------------------
# Einrichtung des ersten Administrators
# ---------------------------------------------------------------------------

def einrichtungscode(app: Flask) -> str:
    """Einmaliger Code für das Anlegen des ersten Administrators. Er steht im Container-Protokoll
    und in der Datei einrichtungscode.txt im Datenordner – nur wer Zugriff auf den Server hat, kennt ihn."""
    code = app.config.get("EINRICHTUNGSCODE")
    if code:
        return code
    datei = _codedatei(app)
    if datei.exists():
        code = datei.read_text(encoding="utf-8").strip()
    if not code:
        code = "-".join(secrets.token_hex(2).upper() for _ in range(3))
        datei.write_text(code + "\n", encoding="utf-8")
        try:
            datei.chmod(0o600)
        except OSError:  # pragma: no cover
            pass
    app.config["EINRICHTUNGSCODE"] = code
    print(f"[{PROGRAMM_NAME}] Einrichtungscode für den ersten Administrator: {code}",
          file=sys.stderr, flush=True)
    return code


def _einrichtung_abschliessen(app: Flask) -> None:
    app.config["EINRICHTUNGSCODE"] = None
    _codedatei(app).unlink(missing_ok=True)


def _codedatei(app: Flask) -> Path:
    # Windows-Server: lokal auf dem Server-PC statt auf dem Netzlaufwerk (das alle Mitarbeitenden lesen)
    return Path(app.config.get("EINRICHTUNGSCODE_ORDNER") or app.config["DATENVERZEICHNIS"]) / EINRICHTUNGSCODE_DATEI


def _ist_lokal() -> bool:
    try:
        return ipaddress.ip_address((request.remote_addr or "").split("%")[0]).is_loopback
    except ValueError:
        return False


def code_noetig() -> bool:
    """Am Server-PC selbst (Windows-Netzwerkversion) geht die Einrichtung ohne Code."""
    return not (current_app.config.get("EINRICHTUNG_LOKAL_OHNE_CODE") and _ist_lokal())


# ---------------------------------------------------------------------------
# Sitzung
# ---------------------------------------------------------------------------

def _sitzung_starten(b) -> None:
    session.clear()
    session.permanent = True
    session["benutzer_id"] = b["id"]
    session["sitzung"] = b["sitzung"]


def aktueller_benutzer():
    bid = session.get("benutzer_id")
    if not bid:
        return None
    b = bn.laden(db.get_db(), bid)
    if b is None or not b["aktiv"] or not secrets.compare_digest(b["sitzung"], session.get("sitzung", "")):
        session.clear()
        return None
    return b


def _weiter(standard: str = "/") -> str:
    ziel = request.values.get("weiter", standard)
    return ziel if ziel.startswith("/") and not ziel.startswith("//") else standard


# ---------------------------------------------------------------------------
# Anbindung an die Anwendung
# ---------------------------------------------------------------------------

def einrichten(app: Flask) -> None:
    app.config.update(
        NETZWERK=True,
        SESSION_COOKIE_NAME="buchhaltung_sitzung",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=app.config.get("NETZWERK_HTTPS", True),
        PERMANENT_SESSION_LIFETIME=timedelta(hours=int(os.environ.get("BUCHHALTUNG_SITZUNG_STUNDEN", "10"))),
    )
    app.config.setdefault("ERLAUBTE_NETZE", netze_aus_text(os.environ.get("BUCHHALTUNG_NETZE")))
    app.register_blueprint(bp)

    # Noch kein Benutzer: Einrichtungscode gleich beim Start ins Protokoll schreiben
    if not app.config["GESPERRT"]:
        con = db.verbinden(app.config["DATENBANK"])
        try:
            if bn.anzahl(con) == 0:
                einrichtungscode(app)
        finally:
            con.close()

    @app.before_request
    def _nur_lokales_netz():
        if not im_lokalen_netz(request.remote_addr, app.config["ERLAUBTE_NETZE"]):
            abort(403)

    # Die Sperre (verschlüsselte Datenbank) wurde vorher registriert und greift zuerst
    @app.before_request
    def _anmeldung():
        g.benutzer = None
        if app.config["GESPERRT"] or request.endpoint in FREI:
            return None
        con = db.get_db()
        if bn.anzahl(con) == 0:
            return redirect(url_for("konto.einrichtung"))
        g.benutzer = aktueller_benutzer()
        if g.benutzer is None:
            if request.method == "GET":
                return redirect(url_for("konto.anmelden", weiter=request.full_path.rstrip("?")))
            return redirect(url_for("konto.anmelden"))
        if not erlaubt(g.benutzer["rolle"], request.method, request.endpoint, request.path):
            abort(403)
        return None

    @app.after_request
    def _sicherheitskoepfe(antwort):
        antwort.headers.setdefault("X-Frame-Options", "DENY")
        antwort.headers.setdefault("X-Content-Type-Options", "nosniff")
        antwort.headers.setdefault("Referrer-Policy", "same-origin")
        if request.endpoint in ("konto.anmelden", "konto.einrichtung", "konto.passwort"):
            antwort.headers["Cache-Control"] = "no-store"
        return antwort

    @app.errorhandler(403)
    def _verboten(_e):
        return render_template("konto/verboten.html"), 403

    @app.context_processor
    def _konto():
        b = g.get("benutzer")
        return {"BENUTZER": b, "ROLLEN": bn.ROLLEN, "IST_ADMIN": bool(b and b["rolle"] == "admin"),
                "DARF_AENDERN": bool(b and b["rolle"] != "lesen")}


# ---------------------------------------------------------------------------
# Seiten
# ---------------------------------------------------------------------------

@bp.route("/anmelden", methods=["GET", "POST"])
def anmelden():
    con = db.get_db()
    if bn.anzahl(con) == 0:
        return redirect(url_for("konto.einrichtung"))
    fehler = ""
    if request.method == "POST":
        try:
            b = bn.anmelden(con, request.form.get("name", ""), request.form.get("passwort", ""))
        except bn.AnmeldeFehler as e:
            fehler = str(e)
        else:
            _sitzung_starten(b)
            return redirect(_weiter())
    return render_template("konto/anmelden.html", fehler=fehler, weiter=_weiter(),
                           name=request.form.get("name", "")), 401 if fehler else 200


@bp.post("/abmelden")
def abmelden():
    session.clear()
    flash("Sie wurden abgemeldet.", "ok")
    return redirect(url_for("konto.anmelden"))


@bp.route("/einrichtung", methods=["GET", "POST"])
def einrichtung():
    con = db.get_db()
    if bn.anzahl(con) > 0:
        return redirect(url_for("konto.anmelden"))
    code = einrichtungscode(current_app)
    mit_code = code_noetig()
    fehler = ""
    if request.method == "POST":
        eingabe = request.form.get("code", "").strip().upper().replace(" ", "")
        if mit_code and not secrets.compare_digest(eingabe, code):
            fehler = "Der Einrichtungscode ist falsch. Er steht im Protokoll des Containers und in der Datei " \
                     f"{EINRICHTUNGSCODE_DATEI} im Datenordner."
        else:
            try:
                bid = bn.anlegen(con, request.form.get("name", ""), request.form.get("anzeigename", ""), "admin",
                                 request.form.get("passwort", ""), request.form.get("wiederholung", ""))
            except bn.BenutzerFehler as e:
                fehler = str(e)
            else:
                con.commit()
                _einrichtung_abschliessen(current_app)
                _sitzung_starten(bn.laden(con, bid))
                flash("Administrator angelegt – Sie sind angemeldet. Legen Sie unter „Benutzer“ weitere Konten an.", "ok")
                return redirect(url_for("start"))
    return render_template("konto/einrichtung.html", fehler=fehler, eingabe=request.form, mit_code=mit_code,
                           passwort_min=bn.PASSWORT_MIN), 400 if fehler else 200


@bp.post("/system/stopp", endpoint="stopp")
def server_stoppen():
    """Beendet den Windows-Server (für Installer, Deinstallation und „Server beenden“).
    Nur vom Server-PC selbst und nur mit dem Steuer-Kennwort aus dem lokalen Benutzerprofil."""
    token = current_app.config.get("STEUER_TOKEN")
    stopp = current_app.config.get("STEUER_STOPP")
    if not token or stopp is None:
        abort(404)
    if not _ist_lokal() or not secrets.compare_digest(request.headers.get("X-Steuer-Token", ""), token):
        abort(403)
    stopp()
    return "beendet"


@bp.route("/konto/passwort", methods=["GET", "POST"])
def passwort():
    fehler = ""
    if request.method == "POST":
        con = db.get_db()
        try:
            sitzung = bn.passwort_setzen(con, g.benutzer["id"], request.form.get("neu", ""),
                                         request.form.get("wiederholung", ""), alt=request.form.get("alt", ""))
        except bn.BenutzerFehler as e:
            fehler = str(e)
        else:
            con.commit()
            session["sitzung"] = sitzung        # diese Anmeldung bleibt, alle anderen enden
            flash("Ihr Passwort wurde geändert. Anmeldungen auf anderen Geräten wurden beendet.", "ok")
            return redirect(url_for("start"))
    return render_template("konto/passwort.html", fehler=fehler, passwort_min=bn.PASSWORT_MIN), 400 if fehler else 200


@bp.get("/zertifikat", endpoint="zertifikat")
def ca_zertifikat():
    """Zertifikat der lokalen Zertifizierungsstelle zum Importieren in Windows/macOS/Browser."""
    pfad = current_app.config.get("ZERTIFIKAT_CA")
    if not pfad or not Path(pfad).exists():
        abort(404)
    return send_file(pfad, mimetype="application/x-x509-ca-cert", as_attachment=True,
                     download_name="buchhaltung-kmu-zertifizierungsstelle.crt")


@bp.get("/benutzer/")
def benutzer_liste():
    con = db.get_db()
    return render_template("konto/benutzer.html", benutzer=bn.alle(con), rollen_text=bn.ROLLEN_TEXT,
                           passwort_min=bn.PASSWORT_MIN, fehler=None, eingabe={})


@bp.post("/benutzer/neu")
def benutzer_neu():
    con = db.get_db()
    f = request.form
    try:
        bn.anlegen(con, f.get("name", ""), f.get("anzeigename", ""), f.get("rolle", ""), f.get("passwort", ""),
                   f.get("wiederholung", ""))
    except bn.BenutzerFehler as e:
        return render_template("konto/benutzer.html", benutzer=bn.alle(con), rollen_text=bn.ROLLEN_TEXT,
                               passwort_min=bn.PASSWORT_MIN, fehler=str(e), eingabe=f), 400
    con.commit()
    flash(f"Benutzer „{f.get('name', '').strip()}“ angelegt.", "ok")
    return redirect(url_for("konto.benutzer_liste"))


@bp.post("/benutzer/<int:benutzer_id>")
def benutzer_aendern(benutzer_id: int):
    con = db.get_db()
    f = request.form
    try:
        bn.aendern(con, benutzer_id, f.get("anzeigename", ""), f.get("rolle", ""), f.get("aktiv") == "1")
        if f.get("passwort"):
            bn.passwort_setzen(con, benutzer_id, f["passwort"])
    except bn.BenutzerFehler as e:
        con.rollback()
        flash(str(e), "fehler")
    else:
        con.commit()
        flash("Gespeichert.", "ok")
    return redirect(url_for("konto.benutzer_liste"))


@bp.get("/benutzer/protokoll")
def gesamtprotokoll():
    con = db.get_db()
    wer = request.args.get("benutzer") or None
    namen = [z[0] for z in con.execute("SELECT DISTINCT benutzer FROM aenderungsprotokoll "
                                       "WHERE benutzer IS NOT NULL ORDER BY benutzer COLLATE NOCASE")]
    return render_template("konto/protokoll.html", eintraege=bn.protokoll_gesamt(con, benutzer=wer),
                           namen=namen, wer=wer)
