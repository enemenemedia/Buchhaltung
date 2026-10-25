"""Prüft XRechnung-Dateien (UBL) gegen XSD, EN 16931 und XRechnung-Regeln.

Prüfstufen – wie beim offiziellen KoSIT-Validator:
  1. XML-Schema OASIS UBL 2.1 (UBL-Invoice-2.1.xsd)
  2. Geschäftsregeln EN 16931 (CEN-Schematron, vorkompiliertes XSLT)
  3. Nationale Regeln XRechnung 3.0 (KoSIT-Schematron, BR-DE-*)

Benötigt: lxml, saxonche (pip install saxonche) und ein Artefakt-Verzeichnis:
  <artefakte>/ubl/maindoc/UBL-Invoice-2.1.xsd        (+ ubl/common/…)  OASIS UBL 2.1
  <artefakte>/EN16931-UBL-validation.xslt                              ConnectingEurope/eInvoicing-EN16931
  <artefakte>/xrechnung/ubl/XRechnung-UBL-validation.sch (+ common.sch) itplr-kosit/xrechnung-schematron
  <artefakte>/skeleton/iso_*.xsl                                        ISO-Schematron-Skeleton (XSLT 2)

Aufruf:
  XRECHNUNG_ARTEFAKTE=/pfad/zu/artefakte python werkzeuge/xrechnung_pruefen.py rechnung.xml [...]
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

SVRL = "{http://purl.oclc.org/dsdl/svrl}"


@dataclass
class Meldung:
    stufe: str
    regel: str
    schwere: str      # fatal | error | warning | information
    text: str
    ort: str = ""


@dataclass
class Ergebnis:
    datei: str
    meldungen: list[Meldung] = field(default_factory=list)

    @property
    def fehler(self) -> list[Meldung]:
        return [m for m in self.meldungen if m.schwere in ("fatal", "error")]

    @property
    def gueltig(self) -> bool:
        return not self.fehler


class Pruefer:
    def __init__(self, artefakte: str | Path):
        self.a = Path(artefakte)
        from saxonche import PySaxonProcessor  # erst hier, damit der Import optional bleibt

        self._proc = PySaxonProcessor(license=False)
        self._xslt = self._proc.new_xslt30_processor()
        self._xsd = etree.XMLSchema(etree.parse(str(self.a / "ubl" / "maindoc" / "UBL-Invoice-2.1.xsd")))
        self._en16931 = self._xslt.compile_stylesheet(stylesheet_file=str(self.a / "EN16931-UBL-validation.xslt"))
        self._xrechnung = self._xslt.compile_stylesheet(stylesheet_text=self._schematron_kompilieren(
            self.a / "xrechnung" / "ubl" / "XRechnung-UBL-validation.sch"))

    def _transform(self, xsl: str, quelle_text: str, basis: Path) -> str:
        ausf = self._xslt.compile_stylesheet(stylesheet_file=str(self.a / "skeleton" / xsl))
        knoten = self._proc.parse_xml(xml_text=quelle_text)
        ausf.set_global_context_item(xdm_item=knoten)
        ausf.set_base_output_uri(basis.as_uri())
        return ausf.transform_to_string(xdm_node=knoten)

    def _schematron_kompilieren(self, sch: Path) -> str:
        # Includes auflösen (relativ zur .sch-Datei), abstrakte Muster expandieren, nach XSLT übersetzen
        ausf = self._xslt.compile_stylesheet(stylesheet_file=str(self.a / "skeleton" / "iso_dsdl_include.xsl"))
        schritt1 = ausf.transform_to_string(source_file=str(sch))
        schritt2 = self._transform("iso_abstract_expand.xsl", schritt1, sch)
        return self._transform("iso_svrl_for_xslt2.xsl", schritt2, sch)

    @staticmethod
    def _svrl_auswerten(svrl: str, stufe: str) -> list[Meldung]:
        baum = etree.fromstring(svrl.encode("utf-8"))
        meldungen = []
        for fa in baum.iter(f"{SVRL}failed-assert", f"{SVRL}successful-report"):
            text = "".join(fa.findtext(f"{SVRL}text") or "").strip()
            meldungen.append(Meldung(stufe, fa.get("id") or "", (fa.get("flag") or "error").lower(),
                                     " ".join(text.split()), fa.get("location") or ""))
        return meldungen

    def pruefen(self, xml: bytes | str | Path, name: str = "") -> Ergebnis:
        if isinstance(xml, Path):
            name, xml = name or xml.name, xml.read_bytes()
        daten = xml.encode("utf-8") if isinstance(xml, str) else xml
        erg = Ergebnis(name or "<xml>")
        try:
            dok = etree.fromstring(daten)
        except etree.XMLSyntaxError as e:
            erg.meldungen.append(Meldung("XML", "SYNTAX", "fatal", str(e)))
            return erg
        if not self._xsd.validate(dok):
            for f in self._xsd.error_log:
                erg.meldungen.append(Meldung("XSD", "UBL-2.1", "fatal", f.message, f"Zeile {f.line}"))
        text = daten.decode("utf-8")
        for stufe, ausf in (("EN16931", self._en16931), ("XRechnung", self._xrechnung)):
            knoten = self._proc.parse_xml(xml_text=text)
            erg.meldungen += self._svrl_auswerten(ausf.transform_to_string(xdm_node=knoten), stufe)
        return erg


def main(argv: list[str]) -> int:
    artefakte = os.environ.get("XRECHNUNG_ARTEFAKTE")
    if not artefakte or len(argv) < 2:
        print(__doc__)
        return 2
    pruefer = Pruefer(artefakte)
    gesamt_ok = True
    for pfad in argv[1:]:
        erg = pruefer.pruefen(Path(pfad))
        gesamt_ok &= erg.gueltig
        print(f"{'GÜLTIG  ' if erg.gueltig else 'UNGÜLTIG'}  {pfad}")
        for m in erg.meldungen:
            print(f"   [{m.schwere:<11}] {m.stufe:<9} {m.regel:<16} {m.text[:160]}")
    return 0 if gesamt_ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
