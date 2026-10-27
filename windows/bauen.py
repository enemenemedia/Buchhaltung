"""Baut die Windows-Installer „X-Rechnung-fuer-KMU-Setup-<Version>.exe“ (Vollversion, nur mit
gültiger Lizenznummer installierbar) und „X-Rechnung-fuer-KMU-Testversion-Setup-<Version>.exe“
(ohne Lizenznummer; erstellt keine XRechnung).

Läuft unter Linux (getestet: Ubuntu 24.04) – kein Windows nötig:
  1. Python 3.12 für Windows (64 Bit) aus conda-forge per micromamba
  2. Python-Pakete als Windows-Wheels von PyPI (pip --platform win_amd64)
  3. Anwendung, Starter, Symbol und – optional – die Regelwerke der XRechnung-Prüfung
  4. Installer mit NSIS (makensis)

Voraussetzungen:  apt install nsis   ·   micromamba (wird bei Bedarf geladen)
Aufruf:
  python3 windows/bauen.py [--pruefung <Ordner mit Prüfartefakten>] [--ausgabe dist] [--variante voll|test|beide]

Die Prüfartefakte erzeugt werkzeuge/pruefung_einrichten.py. Ohne --pruefung wird der
Installer ohne eingebaute XRechnung-Prüfung gebaut (die Erzeugung funktioniert trotzdem).
"""

from __future__ import annotations

import argparse
import bz2
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WINDOWS = REPO / "windows"
PYTHON_VERSION = "3.12"
CONDA = "https://conda.anaconda.org/conda-forge"

# Laufzeit-Pakete (requirements.txt ohne Testwerkzeuge) + Server + Prüfung
PAKETE_PRUEFUNG = ["lxml>=5.0", "saxonche>=12.5"]
PAKETE_EXTRA = ["waitress>=3.0"]

# Was aus der Python-Umgebung nicht gebraucht wird
ENTFERNEN = [
    "Lib/test", "Lib/idlelib", "Lib/tkinter", "Lib/turtledemo", "Lib/ensurepip", "Lib/lib2to3",
    "Lib/venv", "Tools", "include", "Scripts", "conda-meta", "Library/lib/tcl8.6", "Library/lib/tk8.6",
    "Library/include", "Library/share", "Library/lib/pkgconfig", "Lib/site-packages/pip",
    "DLLs/_tkinter.pyd", "Library/bin/tcl86t.dll", "Library/bin/tk86t.dll",
]


def schritt(text: str) -> None:
    print(f"\n==> {text}", flush=True)


def version() -> str:
    m = re.search(r'__version__ = "([^"]+)"', (REPO / "app" / "__init__.py").read_text(encoding="utf-8"))
    return m.group(1)


def micromamba(arbeit: Path) -> Path:
    vorhanden = shutil.which("micromamba")
    if vorhanden:
        return Path(vorhanden)
    ziel = arbeit / "micromamba" / "bin" / "micromamba"
    if ziel.exists():
        return ziel
    schritt("Lade micromamba (conda-forge)")
    index = json.load(urllib.request.urlopen(f"{CONDA}/linux-64/current_repodata.json", timeout=300))
    pakete = [(v["version"], v["build_number"], k) for k, v in index["packages"].items() if v["name"] == "micromamba"]
    datei = max(pakete)[2]
    daten = urllib.request.urlopen(f"{CONDA}/linux-64/{datei}", timeout=300).read()
    with tarfile.open(fileobj=io.BytesIO(bz2.decompress(daten))) as tar:
        tar.extractall(arbeit / "micromamba", filter="data")
    ziel.chmod(0o755)
    return ziel


def python_umgebung(arbeit: Path, ziel: Path) -> None:
    schritt(f"Python {PYTHON_VERSION} für Windows (conda-forge)")
    env = {**os.environ, "MAMBA_ROOT_PREFIX": str(arbeit / "mamba-root")}
    subprocess.run([str(micromamba(arbeit)), "create", "-y", "-q", "-p", str(ziel), "--platform", "win-64",
                    "-c", "conda-forge", "--override-channels", f"python={PYTHON_VERSION}"], check=True, env=env)


def pakete(ziel: Path, mit_pruefung: bool) -> None:
    schritt("Python-Pakete (Windows-Wheels von PyPI)")
    anforderungen = [z.strip() for z in (REPO / "requirements.txt").read_text(encoding="utf-8").splitlines()
                     if z.strip() and not z.startswith("#") and not z.lower().startswith("pytest")]
    anforderungen += PAKETE_EXTRA + (PAKETE_PRUEFUNG if mit_pruefung else [])
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--no-compile", "--disable-pip-version-check",
                    "--target", str(ziel / "Lib" / "site-packages"), "--platform", "win_amd64",
                    "--python-version", PYTHON_VERSION, "--implementation", "cp", "--only-binary=:all:",
                    "--upgrade", *anforderungen], check=True)


def aufraeumen(ziel: Path) -> None:
    schritt("Nicht benötigte Teile entfernen")
    for rel in ENTFERNEN:
        p = ziel / rel
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()
    for p in list(ziel.rglob("__pycache__")):
        shutil.rmtree(p, ignore_errors=True)
    # Debug-Symbole und Kommandozeilenprogramme der Bibliotheken werden nicht gebraucht
    for muster in ("**/*.pdb", "Library/bin/*.exe", "**/*.lib", "**/*.a"):
        for p in ziel.glob(muster):
            p.unlink()
    for p in ziel.glob("Lib/site-packages/*.dist-info/RECORD"):
        p.unlink()


def symbol(ziel: Path) -> None:
    """Programmsymbol (Rechnungsblatt mit €) in mehreren Größen."""
    from PIL import Image, ImageDraw, ImageFont

    gross = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    d = ImageDraw.Draw(gross)
    d.rounded_rectangle((20, 8, 236, 248), radius=28, fill=(31, 111, 92, 255))
    d.polygon([(176, 8), (236, 68), (176, 68)], fill=(23, 50, 43, 255))
    for i, breite in enumerate((120, 150, 100)):
        d.rounded_rectangle((52, 92 + i * 34, 52 + breite, 106 + i * 34), radius=7, fill=(227, 240, 236, 255))
    try:
        schrift = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 88)
    except OSError:
        schrift = ImageFont.load_default()
    d.ellipse((128, 150, 236, 258), fill=(232, 163, 23, 255))
    d.text((182, 206), "€", font=schrift, fill=(23, 50, 43, 255), anchor="mm")
    gross.save(ziel, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


def lizenztext(ziel: Path) -> None:
    teile = [(REPO / "LICENSE").read_text(encoding="utf-8"), "", "-" * 70,
             (WINDOWS / "DRITTANBIETER.txt").read_text(encoding="utf-8")]
    # NSIS zeigt den Lizenztext am zuverlässigsten mit Windows-Zeilenenden an
    ziel.write_text("\r\n".join("\n".join(teile).splitlines()), encoding="utf-8-sig")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--pruefung", help="Ordner mit den Prüfartefakten (siehe werkzeuge/pruefung_einrichten.py)")
    p.add_argument("--ausgabe", default=str(REPO / "dist"))
    p.add_argument("--arbeit", default=str(REPO / "build" / "windows"))
    p.add_argument("--variante", choices=("voll", "test", "beide"), default="beide")
    a = p.parse_args()
    sys.path.insert(0, str(REPO))
    from app import lizenz
    if a.variante != "test" and not lizenz.OEFFENTLICHER_SCHLUESSEL:
        print("In app/lizenz.py ist kein öffentlicher Schlüssel hinterlegt – siehe werkzeuge/lizenz_erzeugen.py.")
        return 1
    if not shutil.which("makensis"):
        print("makensis nicht gefunden – bitte NSIS installieren (z. B. apt install nsis).")
        return 1
    v = version()
    arbeit = Path(a.arbeit).resolve()
    inhalt = arbeit / "inhalt"
    if inhalt.exists():
        shutil.rmtree(inhalt)
    inhalt.mkdir(parents=True)

    python_umgebung(arbeit, inhalt / "python")
    pakete(inhalt / "python", bool(a.pruefung))
    aufraeumen(inhalt / "python")

    schritt("Anwendung, Starter und Symbol")
    programm = inhalt / "programm"
    shutil.copytree(REPO / "app", programm / "app", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    # Das Lizenzwerkzeug gehört nicht zum Kunden (es nützt ohne privaten Schlüssel ohnehin nichts)
    shutil.copytree(REPO / "werkzeuge", programm / "werkzeuge",
                    ignore=shutil.ignore_patterns("__pycache__", "lizenz_erzeugen.py"))
    shutil.copy(WINDOWS / "starter.py", inhalt / "starter.py")
    symbol(inhalt / "xrechnung.ico")
    lizenztext(arbeit / "lizenz.txt")
    if a.pruefung:
        schritt("Regelwerke der XRechnung-Prüfung")
        pruefung = Path(a.pruefung)
        for pflicht in ("EN16931-UBL-validation.xslt", "ubl/maindoc/UBL-Invoice-2.1.xsd",
                        "ubl/maindoc/UBL-CreditNote-2.1.xsd", "xrechnung/ubl/XRechnung-UBL-validation.sch",
                        "skeleton/iso_svrl_for_xslt2.xsl"):
            if not (pruefung / pflicht).is_file():
                print(f"Prüfartefakt fehlt: {pflicht}")
                return 1
        shutil.copytree(pruefung, inhalt / "pruefung")

    schritt("Installer bauen (NSIS)")
    ausgabe = Path(a.ausgabe).resolve()
    ausgabe.mkdir(parents=True, exist_ok=True)
    groesse_kb = sum(f.stat().st_size for f in inhalt.rglob("*") if f.is_file()) // 1024
    varianten = {"voll": [("", f"X-Rechnung-fuer-KMU-Setup-{v}.exe")],
                 "test": [("-DTESTVERSION", f"X-Rechnung-fuer-KMU-Testversion-Setup-{v}.exe")]}
    varianten["beide"] = varianten["voll"] + varianten["test"]
    print()
    for schalter, name in varianten[a.variante]:
        subprocess.run(["makensis", "-V2", f"-DVERSION={v}", f"-DINHALT={inhalt}", f"-DAUSGABE={ausgabe}",
                        f"-DLIZENZ={arbeit / 'lizenz.txt'}", f"-DGROESSE_KB={groesse_kb}",
                        *([schalter] if schalter else []), str(WINDOWS / "installer.nsi")], check=True)
        datei = ausgabe / name
        print(f"Fertig: {datei}  ({datei.stat().st_size / 1_048_576:.1f} MB, entpackt {groesse_kb / 1024:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
