"""Startet die Netzwerkversion als HTTPS-Server (im Docker-Container: python -m app.netzwerk.server).

Umgebungsvariablen:
  BUCHHALTUNG_DATEN       Datenordner (Standard /daten)
  BUCHHALTUNG_PORT        Port (Standard 8443)
  BUCHHALTUNG_HOSTNAMEN   Namen/IP-Adressen, unter denen der Server erreichbar ist, z. B. „nas,nas.local,192.168.1.10“
                          (für das Zertifikat; localhost und 127.0.0.1 sind immer enthalten)
  BUCHHALTUNG_NETZE       erlaubte Netze, z. B. „192.168.1.0/24“ (Standard: alle privaten Netze)
  BUCHHALTUNG_THREADS     gleichzeitige Anfragen (Standard 8)
  BUCHHALTUNG_ZERTIFIKAT / BUCHHALTUNG_SCHLUESSEL
                          eigenes Zertifikat (PEM) statt des selbst erzeugten
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .. import PROGRAMM_NAME, __version__, create_app
from . import zertifikat


def _konfiguration():
    daten = Path(os.environ.get("BUCHHALTUNG_DATEN", "/daten"))
    daten.mkdir(parents=True, exist_ok=True)
    eigenes = os.environ.get("BUCHHALTUNG_ZERTIFIKAT"), os.environ.get("BUCHHALTUNG_SCHLUESSEL")
    if all(eigenes):
        cert, key, ca = eigenes[0], eigenes[1], None
    else:
        z = zertifikat.sicherstellen(daten / "zertifikat")
        cert, key, ca = str(z.server), str(z.schluessel), str(z.ca)
    return daten, cert, key, ca


def main() -> None:  # pragma: no cover – wird im Container ausgeführt
    from gunicorn.app.base import BaseApplication

    daten, cert, key, ca = _konfiguration()
    app = create_app(daten, netzwerk=True, einstellungen={"ZERTIFIKAT_CA": ca})
    port = int(os.environ.get("BUCHHALTUNG_PORT", "8443"))
    optionen = {
        "bind": [f"0.0.0.0:{port}", f"[::]:{port}"] if os.environ.get("BUCHHALTUNG_IPV6") == "1" else [f"0.0.0.0:{port}"],
        # Ein Prozess (das Passwort einer verschlüsselten Datenbank liegt nur in seinem Speicher), mehrere Threads
        "workers": 1,
        "worker_class": "gthread",
        "threads": int(os.environ.get("BUCHHALTUNG_THREADS", "8")),
        "timeout": 120,
        "certfile": cert,
        "keyfile": key,
        "accesslog": "-",
        "errorlog": "-",
        "access_log_format": '%(h)s "%(r)s" %(s)s %(b)s',
        "forwarded_allow_ips": "",
        "limit_request_line": 8190,
    }
    try:  # gunicorn ≥ 25: Steuer-Socket wird nicht gebraucht
        from gunicorn.config import KNOWN_SETTINGS
        if any(x.name == "control_socket_disable" for x in KNOWN_SETTINGS):
            optionen["control_socket_disable"] = True
    except ImportError:  # pragma: no cover
        pass

    class Server(BaseApplication):
        def load_config(self):
            for k, v in optionen.items():
                self.cfg.set(k, v)

        def load(self):
            return app

    print(f"{PROGRAMM_NAME} {__version__} – Netzwerkversion: https://<server>:{port}/  (Daten: {daten})",
          file=sys.stderr, flush=True)
    Server().run()


if __name__ == "__main__":  # pragma: no cover
    main()
