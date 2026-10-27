"""Gemeinsame Hilfen: ein eigenes Schlüsselpaar für Testlizenzen."""

import base64

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app import lizenz


@pytest.fixture(scope="session")
def _schluesselpaar():
    privat = Ed25519PrivateKey.generate()
    oeffentlich = base64.b64encode(privat.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
    return privat, oeffentlich


@pytest.fixture
def lizenz_schluessel(monkeypatch, _schluesselpaar):
    """Privater Schlüssel, dessen öffentlicher Schlüssel im Programm hinterlegt ist."""
    privat, oeffentlich = _schluesselpaar
    monkeypatch.setattr(lizenz, "OEFFENTLICHER_SCHLUESSEL", oeffentlich)
    return privat


@pytest.fixture
def lizenzieren(lizenz_schluessel):
    """lizenzieren(app) – legt eine gültige Lizenz im Datenordner ab und aktiviert sie."""
    def _lizenzieren(app, name="Ene Mene Media GmbH", **kw):
        nummer = lizenz.erzeugen(lizenz_schluessel, name, 42, **kw)
        l = lizenz.pruefen(name, nummer)
        lizenz.speichern(app.config["DATENVERZEICHNIS"], l)
        app.config["LIZENZ"] = l
        return l
    return _lizenzieren
