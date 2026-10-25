// Leistungsformular: Einheiten passend zur Art anzeigen und Gesamtpreis live berechnen.
(function () {
  const form = document.getElementById("leistung-form");
  if (!form) return;
  const einheit = document.getElementById("einheit");
  const preis = document.getElementById("preis");
  const anzahl = document.getElementById("anzahl");
  const ust = document.getElementById("ust");
  const optionen = [...einheit.options];

  // Deutsche und englische Schreibweise lesen (wie app/zahlen.py)
  function zahl(text) {
    let t = String(text || "").replace(/[€\s ]/g, "");
    if (!t) return NaN;
    if (t.includes(",") && t.includes(".")) {
      t = t.lastIndexOf(",") > t.lastIndexOf(".") ? t.replace(/\./g, "").replace(",", ".") : t.replace(/,/g, "");
    } else if (t.includes(",")) {
      t = t.replace(",", ".");
    } else if ((t.match(/\./g) || []).length > 1) {
      t = t.replace(/\./g, "");
    }
    return /^[+-]?\d+(\.\d+)?$/.test(t) ? Number(t) : NaN;
  }
  const euro = new Intl.NumberFormat("de-DE", { style: "currency", currency: "EUR" });
  const dez = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 3 });

  function art() {
    const gewaehlt = form.querySelector('input[name="art"]:checked');
    return gewaehlt ? gewaehlt.value : null;
  }

  function einheitenFiltern() {
    const a = art();
    let ersteSichtbare = null;
    optionen.forEach(o => {
      const passt = o.dataset.art === a;
      o.hidden = !passt;
      o.disabled = !passt;
      if (passt && !ersteSichtbare) ersteSichtbare = o;
    });
    if (einheit.selectedOptions[0]?.disabled && ersteSichtbare) ersteSichtbare.selected = true;
    form.querySelectorAll(".artkarte").forEach(k => k.classList.toggle("gewaehlt", k.querySelector("input").checked));
    rechnen();
  }

  function rechnen() {
    const o = einheit.selectedOptions[0];
    const p = zahl(preis.value), n = zahl(anzahl.value), satz = Number(ust.value);
    form.querySelector(".einheit-name").textContent = o ? o.textContent : "Einheit";
    form.querySelector(".einheit-mehrzahl").textContent = o ? (n === 1 ? o.textContent : o.dataset.mehrzahl) : "";
    const ziel = (id, text) => (document.getElementById(id).textContent = text);
    if (isNaN(p) || isNaN(n)) {
      ziel("rechnung", "–"); ziel("netto", "–"); ziel("brutto", "–");
      return;
    }
    const netto = Math.round(p * n * 100) / 100;
    ziel("rechnung", `${dez.format(n)} × ${euro.format(p)}`);
    ziel("netto", euro.format(netto));
    ziel("brutto", `${euro.format(Math.round(netto * (100 + satz)) / 100)} (inkl. ${satz} % USt)`);
  }

  form.addEventListener("change", e => (e.target.name === "art" ? einheitenFiltern() : rechnen()));
  form.addEventListener("input", rechnen);
  einheitenFiltern();
})();
