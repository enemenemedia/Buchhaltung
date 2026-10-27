"""Lizenznummern für „X-Rechnung für KMU“ erzeugen und prüfen.

Der private Schlüssel ist das einzige, womit sich gültige Lizenznummern erzeugen lassen.
Er gehört NICHT ins Repository und nicht in den Installer – sicher aufbewahren (mit Sicherung).

  # einmalig: neues Schlüsselpaar; trägt den öffentlichen Schlüssel in app/lizenz.py ein
  python werkzeuge/lizenz_erzeugen.py schluessel --privat ~/xrechnung-lizenz/lizenz-privat.pem

  # Lizenz für einen Kunden (Seriennummer wird fortlaufend vergeben)
  python werkzeuge/lizenz_erzeugen.py erzeugen --privat ~/xrechnung-lizenz/lizenz-privat.pem \\
         --name "Bäckerei Sonnenschein GmbH" [--bis 2027-12-31]

  # Lizenz prüfen
  python werkzeuge/lizenz_erzeugen.py pruefen --name "Bäckerei Sonnenschein GmbH" --nummer XRK1-…

Alle erzeugten Lizenzen werden in „lizenzen.csv“ neben dem privaten Schlüssel protokolliert.
Achtung: Ein neues Schlüsselpaar macht alle bisher ausgegebenen Lizenznummern ungültig.
"""

from __future__ import annotations

import argparse
import base64
import csv
import re
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from app import lizenz  # noqa: E402

LIZENZ_PY = REPO / "app" / "lizenz.py"


def privaten_schluessel_laden(pfad: Path):
    from cryptography.hazmat.primitives import serialization

    return serialization.load_pem_private_key(Path(pfad).expanduser().read_bytes(), password=None)


def oeffentlich_base64(privat) -> str:
    from cryptography.hazmat.primitives import serialization as s

    return base64.b64encode(privat.public_key().public_bytes(s.Encoding.Raw, s.PublicFormat.Raw)).decode()


def schluessel(a) -> int:
    from cryptography.hazmat.primitives import serialization as s
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    pfad = Path(a.privat).expanduser()
    if pfad.exists():
        print(f"{pfad} existiert bereits – wird nicht überschrieben.")
        return 1
    pfad.parent.mkdir(parents=True, exist_ok=True)
    privat = Ed25519PrivateKey.generate()
    pfad.write_bytes(privat.private_bytes(s.Encoding.PEM, s.PrivateFormat.PKCS8, s.NoEncryption()))
    try:
        pfad.chmod(0o600)
    except OSError:
        pass
    oeffentlich = oeffentlich_base64(privat)
    text = LIZENZ_PY.read_text(encoding="utf-8")
    text = re.sub(r'^OEFFENTLICHER_SCHLUESSEL = ".*"$', f'OEFFENTLICHER_SCHLUESSEL = "{oeffentlich}"', text,
                  count=1, flags=re.M)
    LIZENZ_PY.write_text(text, encoding="utf-8")
    print(f"Privater Schlüssel: {pfad}  (sicher aufbewahren!)")
    print(f"Öffentlicher Schlüssel in app/lizenz.py eingetragen: {oeffentlich}")
    print("Danach den Installer neu bauen. Bisher ausgegebene Lizenznummern sind ab dieser Version ungültig.")
    return 0


def _register(privat_pfad: Path) -> Path:
    return Path(privat_pfad).expanduser().with_name("lizenzen.csv")


def _naechste_serie(register: Path) -> int:
    if not register.is_file():
        return 1
    with register.open(encoding="utf-8", newline="") as f:
        return max((int(z["seriennummer"]) for z in csv.DictReader(f, delimiter=";")), default=0) + 1


def erzeugen(a) -> int:
    privat = privaten_schluessel_laden(a.privat)
    if oeffentlich_base64(privat) != lizenz.OEFFENTLICHER_SCHLUESSEL:
        print("Warnung: Der private Schlüssel passt nicht zum öffentlichen Schlüssel in app/lizenz.py – "
              "die Lizenz würde vom Programm nicht anerkannt.")
        return 1
    register = _register(a.privat)
    serie = a.serie or _naechste_serie(register)
    bis = date.fromisoformat(a.bis) if a.bis else None
    nummer = lizenz.erzeugen(privat, a.name, serie, gueltig_bis=bis)
    geprueft = lizenz.pruefen(a.name, nummer)
    neu = not register.is_file()
    with register.open("a", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter=";")
        if neu:
            w.writerow(["seriennummer", "lizenznehmer", "ausgestellt", "gueltig_bis", "lizenznummer"])
        w.writerow([serie, geprueft.lizenznehmer, geprueft.ausgestellt.isoformat(),
                    geprueft.gueltig_bis.isoformat() if geprueft.gueltig_bis else "", nummer])
    print(f"Lizenznehmer:  {geprueft.lizenznehmer}")
    print(f"Lizenznummer:  {nummer}")
    print(f"Seriennummer:  {serie}   gültig bis: {geprueft.gueltig_bis or 'unbegrenzt'}")
    return 0


def pruefen(a) -> int:
    try:
        l = lizenz.pruefen(a.name, a.nummer)
    except lizenz.LizenzFehler as e:
        print(f"Ungültig: {e}")
        return 1
    print(f"Gültig – {l.typ_name}, Seriennummer {l.seriennummer}, ausgestellt {l.ausgestellt:%d.%m.%Y}, "
          f"gültig bis {l.gueltig_bis.strftime('%d.%m.%Y') if l.gueltig_bis else 'unbegrenzt'}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    u = p.add_subparsers(dest="befehl", required=True)
    s = u.add_parser("schluessel", help="neues Schlüsselpaar erzeugen")
    s.add_argument("--privat", required=True, help="Datei für den privaten Schlüssel (PEM)")
    s.set_defaults(fn=schluessel)
    e = u.add_parser("erzeugen", help="Lizenznummer erzeugen")
    e.add_argument("--privat", required=True)
    e.add_argument("--name", required=True, help="Lizenznehmer (Firma), erscheint im Programm")
    e.add_argument("--serie", type=int, help="Seriennummer (Standard: fortlaufend)")
    e.add_argument("--bis", help="gültig bis JJJJ-MM-TT (Standard: unbegrenzt)")
    e.set_defaults(fn=erzeugen)
    k = u.add_parser("pruefen", help="Lizenznummer prüfen")
    k.add_argument("--name", required=True)
    k.add_argument("--nummer", required=True)
    k.set_defaults(fn=pruefen)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
