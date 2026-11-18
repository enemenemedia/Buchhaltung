"""Selbst erzeugte Zertifikate für HTTPS im lokalen Netz.

Beim ersten Start entsteht eine eigene, nur für diesen Server gültige Zertifizierungsstelle (CA)
und damit signiert ein Serverzertifikat für die eingetragenen Namen/IP-Adressen. Wer das
CA-Zertifikat einmal auf seinem Rechner als vertrauenswürdig importiert, sieht keine
Browser-Warnung mehr – auch nicht, wenn das Serverzertifikat später erneuert wird.
Der CA-Schlüssel (ca.key) bleibt im Datenordner (nur für den Server lesbar) und darf nicht
weitergegeben werden.

Ordner:  ca.crt / ca.key  (10 Jahre)   server.crt / server.key  (820 Tage, wird erneuert,
         wenn es bald abläuft oder sich die Namen ändern)
"""

from __future__ import annotations

import ipaddress
import os
import socket
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

CA_JAHRE = 10
SERVER_TAGE = 820          # knapp unter 825 Tagen, der Höchstdauer,, die macOS/iOS für Zertifikate eigener CAs akzeptiert
ERNEUERN_TAGE = 30


@dataclass
class Zertifikate:
    ca: Path
    server: Path
    schluessel: Path


def _schreiben(pfad: Path, daten: bytes, geheim: bool = False) -> None:
    tmp = pfad.with_suffix(pfad.suffix + ".neu")
    if geheim:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(daten)
    else:
        tmp.write_bytes(daten)
    os.replace(tmp, pfad)
    if geheim:
        os.chmod(pfad, 0o600)


def _schluessel_pem(k) -> bytes:
    return k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                           serialization.NoEncryption())


def namen_bereinigen(namen) -> tuple[list[str], list]:
    """→ (DNS-Namen, IP-Adressen); localhost und 127.0.0.1 sind immer dabei."""
    dns: list[str] = []
    ips: list = []
    for n in list(namen) + ["localhost", "127.0.0.1"]:
        n = (n or "").strip().strip("[]")
        if not n:
            continue
        try:
            ip = ipaddress.ip_address(n)
        except ValueError:
            if n.lower() not in dns:
                dns.append(n.lower())
        else:
            if ip not in ips:
                ips.append(ip)
    return dns, ips


def standard_namen() -> list[str]:
    """Namen aus BUCHHALTUNG_HOSTNAMEN (Komma-getrennt) plus Rechnername."""
    namen = [n for n in os.environ.get("BUCHHALTUNG_HOSTNAMEN", "").split(",") if n.strip()]
    namen.append(socket.gethostname())
    return namen


def _ca(ordner: Path):
    crt, key = ordner / "ca.crt", ordner / "ca.key"
    if crt.exists() and key.exists():
        return (x509.load_pem_x509_certificate(crt.read_bytes()),
                serialization.load_pem_private_key(key.read_bytes(), None))
    k = ec.generate_private_key(ec.SECP256R1())
    jetzt = datetime.now(timezone.utc)
    name = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, f"Buchhaltung für KMU – lokale CA ({socket.gethostname()[:30]})"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Buchhaltung für KMU (selbst erzeugt)"),
    ])
    c = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(k.public_key())
         .serial_number(x509.random_serial_number())
         .not_valid_before(jetzt - timedelta(minutes=5)).not_valid_after(jetzt + timedelta(days=365 * CA_JAHRE))
         .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
         .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                      content_commitment=False, key_encipherment=False, data_encipherment=False,
                                      key_agreement=False, encipher_only=False, decipher_only=False), critical=True)
         .add_extension(x509.SubjectKeyIdentifier.from_public_key(k.public_key()), critical=False)
         .sign(k, hashes.SHA256()))
    _schreiben(key, _schluessel_pem(k), geheim=True)
    _schreiben(crt, c.public_bytes(serialization.Encoding.PEM))
    return c, k


def _passt(crt: Path, ca, dns: list[str], ips: list) -> bool:
    try:
        c = x509.load_pem_x509_certificate(crt.read_bytes())
        san = c.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except (OSError, ValueError, x509.ExtensionNotFound):
        return False
    return (c.issuer == ca.subject
            and c.not_valid_after_utc - datetime.now(timezone.utc) > timedelta(days=ERNEUERN_TAGE)
            and set(san.get_values_for_type(x509.DNSName)) == set(dns)
            and set(san.get_values_for_type(x509.IPAddress)) == set(ips))


def sicherstellen(ordner: str | Path, namen=None) -> Zertifikate:
    ordner = Path(ordner)
    ordner.mkdir(parents=True, exist_ok=True)
    ca, ca_key = _ca(ordner)
    dns, ips = namen_bereinigen(standard_namen() if namen is None else namen)
    z = Zertifikate(ordner / "ca.crt", ordner / "server.crt", ordner / "server.key")
    if z.schluessel.exists() and _passt(z.server, ca, dns, ips):
        return z
    k = ec.generate_private_key(ec.SECP256R1())
    jetzt = datetime.now(timezone.utc)
    c = (x509.CertificateBuilder()
         .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, (dns or ["localhost"])[0][:64])]))
         .issuer_name(ca.subject).public_key(k.public_key()).serial_number(x509.random_serial_number())
         .not_valid_before(jetzt - timedelta(minutes=5)).not_valid_after(jetzt + timedelta(days=SERVER_TAGE))
         .add_extension(x509.SubjectAlternativeName([x509.DNSName(d) for d in dns]
                                                    + [x509.IPAddress(i) for i in ips]), critical=False)
         .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
         .add_extension(x509.KeyUsage(digital_signature=True, key_encipherment=False, key_cert_sign=False,
                                      crl_sign=False, content_commitment=False, data_encipherment=False,
                                      key_agreement=True, encipher_only=False, decipher_only=False), critical=True)
         .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
         .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
         .sign(ca_key, hashes.SHA256()))
    _schreiben(z.schluessel, _schluessel_pem(k), geheim=True)
    _schreiben(z.server, c.public_bytes(serialization.Encoding.PEM))
    return z
