"""Import von XRechnungen (UBL 2.1 und UN/CEFACT CII) in die Belegerfassung – v. a. Eingangsrechnungen.

Eigene Ausgangsrechnungen werden nicht über diesen Weg, sondern direkt aus dem Modul
„Rechnungen“ übernommen (siehe abgleich.py).
"""

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP

try:
    from defusedxml import ElementTree as ET  # schützt vor XML-Bomben/XXE
except ImportError:  # pragma: no cover
    import xml.etree.ElementTree as ET

NS = {
    "ubl": "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2",
    "ublcn": "urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "rsm": "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100",
    "ram": "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100",
    "udt": "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100",
}


class XRechnungFehler(ValueError):
    pass


@dataclass
class XRechnungDaten:
    format: str                      # "UBL" oder "CII"
    rechnungsnummer: str
    rechnungsdatum: str              # ISO yyyy-mm-dd
    verkaeufer: str
    verkaeufer_ustid: str
    kaeufer: str
    kaeufer_ustid: str
    netto_cent: int
    ust_cent: int
    brutto_cent: int
    waehrung: str
    ust_saetze: list = field(default_factory=list)   # z. B. ["19"]
    gutschrift: bool = False
    positionen: list = field(default_factory=list)   # Bezeichnungen der Positionen
    faellig_am: str = ""
    typcode: str = "380"             # 380 Rechnung, 381 Gutschrift/Storno, 384 Rechnungskorrektur
    bezug: str = ""                  # BG-3: Nummer der Originalrechnung (bei 381/384)

    @property
    def ust_satz(self) -> str:
        """Für die Belegerfassung: '19', '7', '0' oder 'mehrere'."""
        saetze = set(self.ust_saetze) or {"0"}
        if len(saetze) == 1:
            s = saetze.pop()
            return s if s in ("19", "7", "0") else "mehrere"
        return "mehrere"

    @property
    def beschreibung(self) -> str:
        if not self.positionen:
            return f"Rechnung {self.rechnungsnummer}"
        text = self.positionen[0]
        if len(self.positionen) > 1:
            text += f" (+{len(self.positionen) - 1} weitere Positionen)"
        return text[:200]


def _cent(text) -> int:
    if text is None or str(text).strip() == "":
        return 0
    return int((Decimal(str(text).strip()) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _satz(text) -> str:
    if text is None:
        return "0"
    d = Decimal(str(text).strip()).normalize()
    return format(d, "f")


def _t(el, pfad):
    if el is None:
        return ""
    x = el.find(pfad, NS)
    return (x.text or "").strip() if x is not None and x.text else ""


def lese_xrechnung(daten: bytes) -> XRechnungDaten:
    try:
        root = ET.fromstring(daten)
    except Exception as e:
        raise XRechnungFehler(f"Die Datei ist kein gültiges XML: {e}")

    tag = root.tag
    if tag in (f"{{{NS['ubl']}}}Invoice", f"{{{NS['ublcn']}}}CreditNote"):
        return _lese_ubl(root, gutschrift=tag.endswith("CreditNote"))
    if tag == f"{{{NS['rsm']}}}CrossIndustryInvoice":
        return _lese_cii(root)
    raise XRechnungFehler("Unbekanntes Format – erwartet wird eine XRechnung im UBL- oder CII-Format.")


def _ubl_partei(root, rolle):
    p = root.find(f"cac:{rolle}/cac:Party", NS)
    name = _t(p, "cac:PartyLegalEntity/cbc:RegistrationName") or _t(p, "cac:PartyName/cbc:Name")
    ustid = ""
    if p is not None:
        for ts in p.findall("cac:PartyTaxScheme", NS):
            if _t(ts, "cac:TaxScheme/cbc:ID") == "VAT":
                ustid = _t(ts, "cbc:CompanyID")
    return name, ustid


def _lese_ubl(root, gutschrift: bool) -> XRechnungDaten:
    verkaeufer, v_ust = _ubl_partei(root, "AccountingSupplierParty")
    kaeufer, k_ust = _ubl_partei(root, "AccountingCustomerParty")
    waehrung = _t(root, "cbc:DocumentCurrencyCode") or "EUR"

    summen = root.find("cac:LegalMonetaryTotal", NS)
    if summen is None:
        raise XRechnungFehler("UBL: LegalMonetaryTotal fehlt.")
    netto = _cent(_t(summen, "cbc:TaxExclusiveAmount"))
    brutto = _cent(_t(summen, "cbc:TaxInclusiveAmount"))

    ust = 0
    saetze = []
    for tt in root.findall("cac:TaxTotal", NS):
        betrag = tt.find("cbc:TaxAmount", NS)
        if betrag is not None and betrag.get("currencyID", waehrung) == waehrung:
            ust = _cent(betrag.text)
            for sub in tt.findall("cac:TaxSubtotal", NS):
                saetze.append(_satz(_t(sub, "cac:TaxCategory/cbc:Percent") or "0"))

    typcode = _t(root, "cbc:CreditNoteTypeCode" if gutschrift else "cbc:InvoiceTypeCode") or ("381" if gutschrift else "380")
    gutschrift = gutschrift or typcode == "381"
    zeile = "cac:CreditNoteLine" if root.tag.endswith("CreditNote") else "cac:InvoiceLine"
    positionen = [_t(l, "cac:Item/cbc:Name") for l in root.findall(zeile, NS)]

    return XRechnungDaten(
        format="UBL",
        rechnungsnummer=_t(root, "cbc:ID"),
        rechnungsdatum=_t(root, "cbc:IssueDate"),
        verkaeufer=verkaeufer, verkaeufer_ustid=v_ust,
        kaeufer=kaeufer, kaeufer_ustid=k_ust,
        netto_cent=netto, ust_cent=ust, brutto_cent=brutto or netto + ust,
        waehrung=waehrung, ust_saetze=sorted(set(saetze)),
        gutschrift=gutschrift, positionen=[p for p in positionen if p],
        faellig_am=_t(root, "cbc:DueDate") or _t(root, "cac:PaymentMeans/cbc:PaymentDueDate"),
        typcode=typcode, bezug=_t(root, "cac:BillingReference/cac:InvoiceDocumentReference/cbc:ID"),
    )


def _cii_datum(text: str) -> str:
    text = (text or "").strip()
    if len(text) == 8 and text.isdigit():  # Format 102: JJJJMMTT
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text


def _cii_partei(agreement, rolle):
    p = agreement.find(f"ram:{rolle}", NS) if agreement is not None else None
    ustid = ""
    if p is not None:
        for reg in p.findall("ram:SpecifiedTaxRegistration/ram:ID", NS):
            if reg.get("schemeID") == "VA":
                ustid = (reg.text or "").strip()
    return _t(p, "ram:Name"), ustid


def _lese_cii(root) -> XRechnungDaten:
    doc = root.find("rsm:ExchangedDocument", NS)
    trans = root.find("rsm:SupplyChainTradeTransaction", NS)
    if doc is None or trans is None:
        raise XRechnungFehler("CII: ExchangedDocument oder SupplyChainTradeTransaction fehlt.")
    agreement = trans.find("ram:ApplicableHeaderTradeAgreement", NS)
    settlement = trans.find("ram:ApplicableHeaderTradeSettlement", NS)
    if settlement is None:
        raise XRechnungFehler("CII: ApplicableHeaderTradeSettlement fehlt.")

    verkaeufer, v_ust = _cii_partei(agreement, "SellerTradeParty")
    kaeufer, k_ust = _cii_partei(agreement, "BuyerTradeParty")
    waehrung = _t(settlement, "ram:InvoiceCurrencyCode") or "EUR"

    summen = settlement.find("ram:SpecifiedTradeSettlementHeaderMonetarySummation", NS)
    if summen is None:
        raise XRechnungFehler("CII: Summenblock fehlt.")
    netto = _cent(_t(summen, "ram:TaxBasisTotalAmount"))
    brutto = _cent(_t(summen, "ram:GrandTotalAmount"))
    ust = 0
    for tta in summen.findall("ram:TaxTotalAmount", NS):
        if tta.get("currencyID", waehrung) == waehrung:
            ust = _cent(tta.text)

    saetze = [_satz(_t(t, "ram:RateApplicablePercent") or "0")
              for t in settlement.findall("ram:ApplicableTradeTax", NS)]
    positionen = [_t(l, "ram:SpecifiedTradeProduct/ram:Name")
                  for l in trans.findall("ram:IncludedSupplyChainTradeLineItem", NS)]

    typ = _t(doc, "ram:TypeCode")
    return XRechnungDaten(
        format="CII",
        rechnungsnummer=_t(doc, "ram:ID"),
        rechnungsdatum=_cii_datum(_t(doc, "ram:IssueDateTime/udt:DateTimeString")),
        verkaeufer=verkaeufer, verkaeufer_ustid=v_ust,
        kaeufer=kaeufer, kaeufer_ustid=k_ust,
        netto_cent=netto, ust_cent=ust, brutto_cent=brutto or netto + ust,
        waehrung=waehrung, ust_saetze=sorted(set(saetze)),
        gutschrift=(typ == "381"), positionen=[p for p in positionen if p],
        faellig_am=_cii_datum(_t(settlement, "ram:SpecifiedTradePaymentTerms/ram:DueDateDateTime/udt:DateTimeString")),
        typcode=typ or "380", bezug=_t(settlement, "ram:InvoiceReferencedDocument/ram:IssuerAssignedID"),
    )


def ist_eigene_rechnung(x: XRechnungDaten, firma_name: str, firma_ustid: str) -> bool:
    """True, wenn wir der Verkäufer sind (-> Einnahme), sonst Ausgabe."""
    if firma_ustid and x.verkaeufer_ustid and firma_ustid.replace(" ", "").upper() == x.verkaeufer_ustid.replace(" ", "").upper():
        return True
    if firma_name and x.verkaeufer and firma_name.strip().lower() in x.verkaeufer.lower():
        return True
    return False
