"""Richtet die offizielle XRechnung-Prüfung ein (einmalig, benötigt git und Internet).

Lädt aus den Originalquellen:
  - EN-16931-Regeln (UBL)       github.com/ConnectingEurope/eInvoicing-EN16931
  - XRechnung-Regeln (KoSIT)    github.com/itplr-kosit/xrechnung-schematron
  - ISO-Schematron-Compiler     github.com/Schematron/schematron
  - UBL-2.1-Schemas (OASIS)     docs.oasis-open.org/ubl/os-UBL-2.1/UBL-2.1.zip

Aufruf:
  pip install -r requirements-pruefung.txt
  python werkzeuge/pruefung_einrichten.py [Zielordner]          (Vorgabe: daten/xrechnung-pruefung)
  python werkzeuge/pruefung_einrichten.py Ziel --ubl-zip UBL-2.1.zip   (ZIP vorher selbst geladen)

Danach die Umgebungsvariable setzen, z. B.
  Linux/macOS:  export XRECHNUNG_ARTEFAKTE=daten/xrechnung-pruefung
  Windows:      set XRECHNUNG_ARTEFAKTE=daten\\xrechnung-pruefung
Ist sie gesetzt, prüft die Anwendung jede XRechnung vor dem Abschließen gegen diese Regeln,
und die Tests prüfen alle erzeugten XML-Dateien.
"""

from __future__ import annotations

import argparse
import io
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

QUELLEN = {
    "en16931": ("https://github.com/ConnectingEurope/eInvoicing-EN16931", ["ubl/xslt"]),
    "xrechnung": ("https://github.com/itplr-kosit/xrechnung-schematron", ["src/validation/schematron"]),
    "schematron": ("https://github.com/Schematron/schematron", ["trunk/schematron/code"]),
}
UBL_ZIP = "https://docs.oasis-open.org/ubl/os-UBL-2.1/UBL-2.1.zip"


def _git_sparse(url: str, pfade: list[str], ziel: Path) -> None:
    subprocess.run(["git", "clone", "-q", "--depth", "1", "--filter=blob:none", "--sparse", url, str(ziel)], check=True)
    subprocess.run(["git", "-C", str(ziel), "sparse-checkout", "set", *pfade], check=True)


def _ubl_entpacken(daten: bytes, ziel: Path) -> None:
    with zipfile.ZipFile(io.BytesIO(daten)) as z:
        for name in z.namelist():
            teile = Path(name).parts
            if "xsd" in teile and name.endswith(".xsd"):
                rest = Path(*teile[teile.index("xsd") + 1:])
                (ziel / rest).parent.mkdir(parents=True, exist_ok=True)
                (ziel / rest).write_bytes(z.read(name))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("ziel", nargs="?", default="daten/xrechnung-pruefung")
    p.add_argument("--ubl-zip", help="lokal gespeicherte UBL-2.1.zip statt Download")
    a = p.parse_args()
    ziel = Path(a.ziel).resolve()
    ziel.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        for name, (url, pfade) in QUELLEN.items():
            print(f"Lade {url} …")
            _git_sparse(url, pfade, tmp / name)
        shutil.copy(tmp / "en16931/ubl/xslt/EN16931-UBL-validation.xslt", ziel)
        shutil.rmtree(ziel / "xrechnung", ignore_errors=True)
        shutil.copytree(tmp / "xrechnung/src/validation/schematron", ziel / "xrechnung")
        (ziel / "skeleton").mkdir(exist_ok=True)
        for f in ("iso_dsdl_include.xsl", "iso_abstract_expand.xsl", "iso_svrl_for_xslt2.xsl",
                  "iso_schematron_skeleton_for_saxon.xsl"):
            shutil.copy(tmp / "schematron/trunk/schematron/code" / f, ziel / "skeleton")

    print("Lade UBL-2.1-Schemas (OASIS) …")
    try:
        daten = Path(a.ubl_zip).read_bytes() if a.ubl_zip else urllib.request.urlopen(UBL_ZIP, timeout=120).read()
    except OSError as e:
        print(f"UBL-Schemas konnten nicht geladen werden ({e}).\n"
              f"Bitte {UBL_ZIP} im Browser herunterladen und mit --ubl-zip angeben.")
        return 1
    _ubl_entpacken(daten, ziel / "ubl")
    if not (ziel / "ubl/maindoc/UBL-Invoice-2.1.xsd").is_file():
        print("UBL-Invoice-2.1.xsd wurde im ZIP nicht gefunden.")
        return 1
    print(f"Fertig: {ziel}\nUmgebungsvariable setzen: XRECHNUNG_ARTEFAKTE={ziel}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
