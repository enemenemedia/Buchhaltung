"""Baut „X-Rechnung für KMU“ für macOS – Vollversion und Testversion als ZIP mit App-Bundle.

Läuft unter Linux (wie windows/bauen.py) – kein Mac nötig:
  1. Python 3.12 für macOS aus conda-forge per micromamba, je einmal für Apple Silicon (osx-arm64)
     und Intel (osx-64). Das Programm wählt beim Start die passende Laufzeit.
  2. Python-Pakete als macOS-Wheels von PyPI (pip --platform)
  3. App-Bundle „X-Rechnung für KMU.app“ mit Starter, Symbol und – optional – den Regelwerken
     der XRechnung-Prüfung
  4. Alle Mach-O-Dateien ad hoc signieren (rcodesign). Nötig, weil micromamba beim Einrichten
     Pfade in den Programmdateien ersetzt; Apple Silicon startet Programme mit ungültiger
     Signatur nicht. Eine Developer-ID-Signatur mit Notarisierung ersetzt das nicht (siehe README).
  5. ZIP (mit Symlinks und Dateirechten, wie „ditto -c -k“)

Aufruf:
  python3 macos/bauen.py [--pruefung <Ordner>] [--ausgabe dist] [--variante voll|test|beide]

Ergebnis: dist/X-Rechnung-fuer-KMU-<Version>-macOS.zip und
          dist/X-Rechnung-fuer-KMU-Testversion-<Version>-macOS.zip
"""

from __future__ import annotations

import argparse
import io
import os
import plistlib
import shutil
import stat
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MACOS = REPO / "macos"

def _windows_bauen():
    """windows/bauen.py (micromamba, Symbol, Paketlisten, Version) unter eigenem Namen laden."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("windows_bauen", REPO / "windows" / "bauen.py")
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


windows = _windows_bauen()

APP = "X-Rechnung für KMU.app"
BUNDLE_ID = "de.ene-mene-media.xrechnung-kmu"
PYTHON_VERSION = windows.PYTHON_VERSION
# Prozessor → (conda-Plattform, Wheel-Plattform)
ARCHITEKTUREN = {"arm64": ("osx-arm64", "macosx_11_0_arm64"), "x86_64": ("osx-64", "macosx_10_15_x86_64")}
MIN_MACOS = "10.15"
RCODESIGN_URL = ("https://github.com/indygreg/apple-platform-rs/releases/download/apple-codesign%2F0.29.0/"
                 "apple-codesign-0.29.0-x86_64-unknown-linux-musl.tar.gz")

PY = f"python{PYTHON_VERSION}"
ENTFERNEN = [
    f"lib/{PY}/test", f"lib/{PY}/idlelib", f"lib/{PY}/tkinter", f"lib/{PY}/turtledemo", f"lib/{PY}/ensurepip",
    f"lib/{PY}/lib2to3", f"lib/{PY}/venv", f"lib/{PY}/site-packages/pip", f"lib/{PY}/config-{PYTHON_VERSION}-darwin",
    "lib/tcl8.6", "lib/tk8.6", "lib/itcl4.2.3", "lib/thread2.8.7", "lib/tdbc1.1.5", "lib/pkgconfig", "lib/cmake",
    "include", "share", "conda-meta", "man", "ssl/misc", "lib/tcl8", f"lib/{PY}/site-packages/setuptools",
    f"lib/{PY}/site-packages/_distutils_hack", f"lib/{PY}/site-packages/wheel", f"lib/{PY}/site-packages/distutils-precedence.pth",
]
VARIANTEN = {"voll": "X-Rechnung-fuer-KMU-{v}-macOS.zip", "test": "X-Rechnung-fuer-KMU-Testversion-{v}-macOS.zip"}

STARTSKRIPT = """#!/bin/bash
# Startet X-Rechnung für KMU mit der Python-Laufzeit für diesen Prozessor.
R="$(cd "$(dirname "$0")/../Resources" && pwd)"
if [ "$(uname -m)" = "arm64" ] && [ -x "$R/python-arm64/bin/python3" ]; then
  PY="$R/python-arm64/bin/python3"
else
  PY="$R/python-x86_64/bin/python3"
fi
# -I: keine fremden Python-Einstellungen; -B: nichts ins (signierte) App-Bundle schreiben
exec "$PY" -I -B "$R/starter.py" "$@"
"""

LIESMICH = """X-Rechnung für KMU {v}{zusatz} für macOS
==========================================

Installation
  1. „X-Rechnung für KMU“ in den Ordner „Programme“ ziehen.
  2. Beim ersten Öffnen meldet macOS, dass der Entwickler nicht überprüft werden kann
     (das Programm ist noch nicht von Apple notarisiert):
     - macOS 15 und neuer: Programm einmal öffnen, die Meldung schließen, dann
       Systemeinstellungen → Datenschutz & Sicherheit → „Dennoch öffnen“.
     - macOS 14 und älter: Programm mit gedrückter ctrl-Taste anklicken → „Öffnen“ → „Öffnen“.
  3. Beim ersten Start wählen Sie den Datenordner (Vorschlag: Dokumente/X-Rechnung){lizenz}

Das Programm läuft im Browser (nur auf diesem Mac, 127.0.0.1). Beendet wird es über
„Beenden“ oben rechts in der Oberfläche. Ein Symbol im Dock erscheint nicht.

Update: das neue Programm in „Programme“ ersetzen. Datenordner und Lizenz bleiben erhalten.
Deinstallation: das Programm in den Papierkorb legen. Ihre Daten bleiben im Datenordner
(gesetzliche Aufbewahrungsfristen beachten).
"""


def schritt(text: str) -> None:
    print(f"\n==> {text}", flush=True)


def rcodesign(arbeit: Path) -> Path:
    vorhanden = shutil.which("rcodesign")
    if vorhanden:
        return Path(vorhanden)
    ziel = arbeit / "rcodesign" / "rcodesign"
    if ziel.exists():
        return ziel
    schritt("Lade rcodesign (Signierwerkzeug für macOS-Programme)")
    daten = urllib.request.urlopen(RCODESIGN_URL, timeout=300).read()
    with tarfile.open(fileobj=io.BytesIO(daten)) as tar:
        mitglied = next(m for m in tar.getmembers() if m.name.endswith("/rcodesign"))
        mitglied.name = "rcodesign"
        tar.extract(mitglied, ziel.parent, filter="data")
    ziel.chmod(0o755)
    return ziel


def python_umgebung(arbeit: Path, ziel: Path, conda_plattform: str) -> None:
    schritt(f"Python {PYTHON_VERSION} für macOS ({conda_plattform}, conda-forge)")
    env = {**os.environ, "MAMBA_ROOT_PREFIX": str(arbeit / "mamba-root")}
    subprocess.run([str(windows.micromamba(arbeit)), "create", "-y", "-q", "-p", str(ziel), "--platform",
                    conda_plattform, "-c", "conda-forge", "--override-channels", f"python={PYTHON_VERSION}"],
                   check=True, env=env)


def pakete(ziel: Path, wheel_plattform: str, mit_pruefung: bool) -> None:
    schritt(f"Python-Pakete (Wheels {wheel_plattform})")
    anforderungen = [z.strip() for z in (REPO / "requirements.txt").read_text(encoding="utf-8").splitlines()
                     if z.strip() and not z.startswith("#") and not z.lower().startswith("pytest")]
    anforderungen += windows.PAKETE_EXTRA + (windows.PAKETE_PRUEFUNG if mit_pruefung else [])
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--no-compile", "--disable-pip-version-check",
                    "--target", str(ziel / "lib" / PY / "site-packages"), "--platform", wheel_plattform,
                    "--python-version", PYTHON_VERSION, "--implementation", "cp", "--only-binary=:all:",
                    "--upgrade", *anforderungen], check=True)


def aufraeumen(ziel: Path) -> None:
    schritt("Nicht benötigte Teile entfernen")
    for rel in ENTFERNEN:
        p = ziel / rel
        if p.is_dir() and not p.is_symlink():
            shutil.rmtree(p)
        elif p.exists() or p.is_symlink():
            p.unlink()
    # Nur der Python-Interpreter wird gebraucht, nicht die übrigen Kommandozeilenprogramme
    for p in (ziel / "bin").iterdir():
        if p.name not in ("python3", PY, "python"):
            p.unlink()
    for muster in ("lib/libtcl*", "lib/libtk*", "lib/sqlite3.*", f"lib/{PY}/site-packages/setuptools-*",
                   f"lib/{PY}/site-packages/wheel-*", f"lib/{PY}/lib-dynload/_tkinter*", "lib/*.a", "**/*.a",
                   "**/__pycache__", "**/*.pyc", f"lib/{PY}/site-packages/*.dist-info/RECORD",
                   f"lib/{PY}/site-packages/bin"):
        for p in list(ziel.glob(muster)):
            if p.is_dir() and not p.is_symlink():
                shutil.rmtree(p, ignore_errors=True)
            elif p.exists() or p.is_symlink():
                p.unlink()


def ist_macho(pfad: Path) -> bool:
    if pfad.is_symlink() or not pfad.is_file():
        return False
    with pfad.open("rb") as f:
        kopf = f.read(8)
    if len(kopf) < 8:
        return False
    if kopf[:4] in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xfe\xed\xfa\xce"):
        return True
    # Universal-Binary (0xCAFEBABE) – nicht mit Java-Klassendateien verwechseln (gleiche Kennung)
    return kopf[:4] == b"\xca\xfe\xba\xbe" and int.from_bytes(kopf[4:8], "big") < 30


def signieren(werkzeug: Path, ordner: Path) -> int:
    dateien = [p for p in sorted(ordner.rglob("*")) if ist_macho(p)]
    schritt(f"{len(dateien)} Programmdateien ad hoc signieren")
    for p in dateien:
        subprocess.run([str(werkzeug), "sign", str(p)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    fehler = [f"{p.relative_to(ordner)}: {m}" for p in dateien if (m := signatur_pruefen(p))]
    if fehler:
        raise SystemExit("Ungültige Signaturen:\n" + "\n".join(fehler[:20]))
    print(f"Alle {len(dateien)} Signaturen geprüft (Seiten-Prüfsummen stimmen).")
    return len(dateien)


def _scheibe_pruefen(daten: bytes) -> str | None:
    """Prüft eine einzelne Mach-O-Datei: Signatur vorhanden und Prüfsummen aller Codeseiten korrekt.

    (Das ist die Prüfung, an der der macOS-Kern auf Apple Silicon Programme scheitern lässt.
    „rcodesign verify“ meldet bei Ad-hoc-Signaturen fälschlich Fehler.)"""
    import hashlib
    import struct

    magic = daten[:4]
    if magic in (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe"):
        e = "<"
    elif magic in (b"\xfe\xed\xfa\xcf", b"\xfe\xed\xfa\xce"):
        e = ">"
    else:
        return "keine Mach-O-Datei"
    kopf = 32 if magic in (b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf") else 28
    ncmds = struct.unpack_from(e + "I", daten, 16)[0]
    pos, sig = kopf, None
    for _ in range(ncmds):
        cmd, groesse = struct.unpack_from(e + "II", daten, pos)
        if cmd == 0x1D:                                   # LC_CODE_SIGNATURE
            sig = struct.unpack_from(e + "II", daten, pos + 8)
        pos += groesse
    if sig is None:
        return "nicht signiert"
    off, _laenge = sig
    sb_magic, _, anzahl = struct.unpack_from(">III", daten, off)
    if sb_magic != 0xFADE0CC0:
        return "Signaturblock beschädigt"
    verzeichnisse = []
    for i in range(anzahl):
        typ, rel = struct.unpack_from(">II", daten, off + 12 + 8 * i)
        if typ == 0 or 0x1000 <= typ < 0x1005:            # CodeDirectory und alternative
            verzeichnisse.append(off + rel)
    if not verzeichnisse:
        return "kein CodeDirectory"
    for cd in verzeichnisse:
        (cd_magic, _, _, flags, hash_off, _, _, n_code, code_limit, hash_groesse, hash_typ, _,
         seite_log2) = struct.unpack_from(">IIIIIIIIIBBBB", daten, cd)
        if cd_magic != 0xFADE0C02:
            return "CodeDirectory beschädigt"
        algo = {1: hashlib.sha1, 2: hashlib.sha256}.get(hash_typ)
        if algo is None:
            continue
        seite = 1 << seite_log2 if seite_log2 else code_limit
        for i in range(n_code):
            stueck = daten[i * seite:min((i + 1) * seite, code_limit)]
            soll = daten[cd + hash_off + i * hash_groesse:cd + hash_off + (i + 1) * hash_groesse]
            if algo(stueck).digest()[:hash_groesse] != soll:
                return f"Prüfsumme von Seite {i} falsch"
    return None


def signatur_pruefen(pfad: Path) -> str | None:
    import struct

    daten = pfad.read_bytes()
    if daten[:4] == b"\xca\xfe\xba\xbe":                  # Universal-Binary: jede Architektur prüfen
        for i in range(struct.unpack_from(">I", daten, 4)[0]):
            _, _, off, groesse, _ = struct.unpack_from(">iiIII", daten, 8 + 20 * i)
            if (m := _scheibe_pruefen(daten[off:off + groesse])):
                return m
        return None
    return _scheibe_pruefen(daten)


def info_plist(v: str) -> dict:
    return {
        "CFBundleDevelopmentRegion": "de",
        "CFBundleDisplayName": "X-Rechnung für KMU",
        "CFBundleName": "X-Rechnung",
        "CFBundleExecutable": "X-Rechnung",
        "CFBundleIconFile": "xrechnung",
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": v,
        "CFBundleVersion": v,
        "LSMinimumSystemVersion": MIN_MACOS,
        "LSApplicationCategoryType": "public.app-category.business",
        # Kein eigenes Fenster: Die Oberfläche läuft im Browser, beendet wird über „Beenden“
        "LSUIElement": True,
        "NSHighResolutionCapable": True,
        "NSHumanReadableCopyright": "© Ene Mene Media – MIT-Lizenz",
    }


def bundle(inhalt: Path, ziel: Path, laufzeiten: Path, v: str, variante: str, pruefung: Path | None) -> None:
    contents = ziel / "Contents"
    res = contents / "Resources"
    (contents / "MacOS").mkdir(parents=True)
    res.mkdir()
    with (contents / "Info.plist").open("wb") as f:
        plistlib.dump(info_plist(v), f)
    (contents / "PkgInfo").write_text("APPL????", encoding="ascii")
    start = contents / "MacOS" / "X-Rechnung"
    start.write_text(STARTSKRIPT, encoding="utf-8")
    start.chmod(0o755)
    for arch in ARCHITEKTUREN:
        shutil.copytree(laufzeiten / f"python-{arch}", res / f"python-{arch}", symlinks=True)
    shutil.copytree(inhalt / "programm", res / "programm")
    shutil.copy(MACOS / "starter.py", res / "starter.py")
    shutil.copy(inhalt / "xrechnung.icns", res / "xrechnung.icns")
    (res / "variante.txt").write_text(variante + "\n", encoding="utf-8")
    if pruefung:
        shutil.copytree(pruefung, res / "pruefung")


def zip_schreiben(quelle: Path, ziel: Path, extra: dict[str, str]) -> None:
    """ZIP mit Unix-Rechten und Symlinks (entpackt im Finder wie „ditto -x -k“)."""
    with zipfile.ZipFile(ziel, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        basis = quelle.parent
        for pfad in [quelle, *sorted(quelle.rglob("*"))]:
            name = pfad.relative_to(basis).as_posix()
            st = pfad.lstat()
            zeit = time.localtime(max(st.st_mtime, 315532800))[:6]      # ZIP kennt nichts vor 1980
            if stat.S_ISLNK(st.st_mode):
                info = zipfile.ZipInfo(name, zeit)
                info.create_system = 3
                info.external_attr = (stat.S_IFLNK | 0o755) << 16
                z.writestr(info, os.readlink(pfad))
            elif stat.S_ISDIR(st.st_mode):
                info = zipfile.ZipInfo(name + "/", zeit)
                info.create_system = 3
                info.external_attr = (stat.S_IFDIR | 0o755) << 16 | 0x10
                z.writestr(info, b"")
            else:
                info = zipfile.ZipInfo.from_file(pfad, name)
                info.create_system = 3
                info.compress_type = zipfile.ZIP_DEFLATED
                modus = 0o755 if st.st_mode & 0o111 else 0o644
                info.external_attr = (stat.S_IFREG | modus) << 16
                with pfad.open("rb") as f:
                    z.writestr(info, f.read(), compresslevel=9)
        for name, text in extra.items():
            info = zipfile.ZipInfo(name, time.localtime()[:6])
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            z.writestr(info, text.encode("utf-8"), compress_type=zipfile.ZIP_DEFLATED)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--pruefung", help="Ordner mit den Prüfartefakten (siehe werkzeuge/pruefung_einrichten.py)")
    p.add_argument("--ausgabe", default=str(REPO / "dist"))
    p.add_argument("--arbeit", default=str(REPO / "build" / "macos"))
    p.add_argument("--variante", choices=("voll", "test", "beide"), default="beide")
    a = p.parse_args()
    sys.path.insert(0, str(REPO))
    from app import lizenz
    if a.variante != "test" and not lizenz.OEFFENTLICHER_SCHLUESSEL:
        print("In app/lizenz.py ist kein öffentlicher Schlüssel hinterlegt – siehe werkzeuge/lizenz_erzeugen.py.")
        return 1
    pruefung = Path(a.pruefung).resolve() if a.pruefung else None
    if pruefung:
        for pflicht in ("EN16931-UBL-validation.xslt", "ubl/maindoc/UBL-Invoice-2.1.xsd",
                        "xrechnung/ubl/XRechnung-UBL-validation.sch", "skeleton/iso_svrl_for_xslt2.xsl"):
            if not (pruefung / pflicht).is_file():
                print(f"Prüfartefakt fehlt: {pflicht}")
                return 1

    v = windows.version()
    arbeit = Path(a.arbeit).resolve()
    laufzeiten = arbeit / "laufzeiten"
    inhalt = arbeit / "inhalt"
    for ordner in (laufzeiten, inhalt):
        if ordner.exists():
            shutil.rmtree(ordner)
    laufzeiten.mkdir(parents=True)
    inhalt.mkdir(parents=True)
    werkzeug = rcodesign(arbeit)

    for arch, (conda_plattform, wheel_plattform) in ARCHITEKTUREN.items():
        ziel = laufzeiten / f"python-{arch}"
        python_umgebung(arbeit, ziel, conda_plattform)
        pakete(ziel, wheel_plattform, bool(pruefung))
        aufraeumen(ziel)
    signieren(werkzeug, laufzeiten)

    schritt("Anwendung und Symbol")
    shutil.copytree(REPO / "app", inhalt / "programm" / "app", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(REPO / "werkzeuge", inhalt / "programm" / "werkzeuge",
                    ignore=shutil.ignore_patterns("__pycache__", "lizenz_erzeugen.py"))
    windows.symbol_bild(1024).save(inhalt / "xrechnung.icns")
    lizenz_text = (REPO / "LICENSE").read_text(encoding="utf-8") + "\n" + "-" * 70 + "\n" + \
        (MACOS / "DRITTANBIETER.txt").read_text(encoding="utf-8")

    ausgabe = Path(a.ausgabe).resolve()
    ausgabe.mkdir(parents=True, exist_ok=True)
    for variante in (("voll", "test") if a.variante == "beide" else (a.variante,)):
        schritt(f"App-Bundle und ZIP ({'Vollversion' if variante == 'voll' else 'Testversion'})")
        ordner = arbeit / f"bundle-{variante}"
        if ordner.exists():
            shutil.rmtree(ordner)
        ordner.mkdir()
        app = ordner / APP
        bundle(inhalt, app, laufzeiten, v, variante, pruefung)
        voll = variante == "voll"
        liesmich = LIESMICH.format(
            v=v, zusatz="" if voll else " (Testversion)",
            lizenz=" und geben\n     Lizenznehmer und Lizenznummer aus Ihrer Lizenz-E-Mail ein. Ohne gültige Lizenz\n"
                   "     startet die Vollversion nicht." if voll else
                   ".\n\n  Testversion: Alle Funktionen sind nutzbar, XRechnungen (XML) werden jedoch nicht\n"
                   "  erstellt. Eine Lizenznummer können Sie im Programm unter „Einstellungen“ eingeben.")
        datei = ausgabe / VARIANTEN[variante].format(v=v)
        datei.unlink(missing_ok=True)
        zip_schreiben(app, datei, {"LIESMICH.txt": liesmich, "LIZENZEN.txt": lizenz_text})
        groesse = sum(f.stat().st_size for f in app.rglob("*") if f.is_file() and not f.is_symlink())
        print(f"Fertig: {datei}  ({datei.stat().st_size / 1_048_576:.1f} MB, entpackt {groesse / 1_048_576:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
