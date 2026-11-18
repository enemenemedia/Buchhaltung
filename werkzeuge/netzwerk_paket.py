"""Erzeugt das Auslieferungspaket der Netzwerkversion:
dist/Buchhaltung-fuer-KMU-Netzwerk-<Version>.zip

Inhalt: docker-compose.yml (fertig zum Anpassen), ANLEITUNG.md und alle Quellen zum
Selbstbauen des Images (app/, netzwerk/Dockerfile, netzwerk/entrypoint.sh, requirements-netzwerk.txt).

    python werkzeuge/netzwerk_paket.py
"""

from __future__ import annotations

import hashlib
import re
import sys
import zipfile
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WURZEL))

from app import __version__  # noqa: E402

ORDNER = "buchhaltung-kmu-netzwerk"


def _dateien():
    for p in sorted((WURZEL / "app").rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc":
            yield p, p.relative_to(WURZEL).as_posix()
    for name in ("netzwerk/Dockerfile", "netzwerk/entrypoint.sh", "requirements-netzwerk.txt", "LICENSE"):
        yield WURZEL / name, name


def compose_fuer_paket() -> str:
    text = (WURZEL / "netzwerk" / "docker-compose.yml").read_text(encoding="utf-8")
    text = re.sub(r"buchhaltung:\d+\.\d+\.\d+", f"buchhaltung:{__version__}", text)
    # im Paket liegt das Dockerfile relativ zur compose-Datei
    return text.replace("build: { context: .., dockerfile: netzwerk/Dockerfile }",
                        "build: { context: ., dockerfile: netzwerk/Dockerfile }")


def bauen() -> Path:
    ziel = WURZEL / "dist" / f"Buchhaltung-fuer-KMU-Netzwerk-{__version__}.zip"
    ziel.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(ziel, "w", zipfile.ZIP_DEFLATED) as z:
        def rein(name, daten, ausfuehrbar=False):
            info = zipfile.ZipInfo(f"{ORDNER}/{name}", date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o755 if ausfuehrbar else 0o644) << 16
            z.writestr(info, daten)

        rein("docker-compose.yml", compose_fuer_paket())
        rein("ANLEITUNG.md", (WURZEL / "netzwerk" / "ANLEITUNG.md").read_bytes())
        for pfad, name in _dateien():
            rein(name, pfad.read_bytes(), ausfuehrbar=name.endswith(".sh"))
    summe = hashlib.sha256(ziel.read_bytes()).hexdigest()
    (ziel.parent / f"SHA256SUMS-Netzwerk-{__version__}.txt").write_text(f"{summe}  {ziel.name}\n", encoding="utf-8")
    return ziel


if __name__ == "__main__":
    pfad = bauen()
    print(pfad, pfad.stat().st_size // 1024, "KB")
