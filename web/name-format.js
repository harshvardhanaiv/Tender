/* One way to show a buyer or supplier name.

   Portals publish the same organisation as "LEAP LEGAL SOFTWARE LTD", "London Borough Of Camden" and "Gristwood and Toms:".
   The stored name is never changed (it is the key people search by); this only tidies how it is SHOWN, in Buyer Intelligence,
   Supplier Intelligence and the Buyer workspace:
     - a name in capitals is title-cased, keeping acronyms (NHS, UK, PLC, LLP, CIC ...) and small words ("of", "and") in lower case;
     - "Of", "On", "And", "The" in the middle of a mixed-case name go to lower case;
     - trailing punctuation left by a bad scrape (":" "," ";") is dropped, and runs of spaces collapse.
   A mixed-case name ("McDonald & Sons", "iSOFT Ltd") is otherwise left exactly as published. */
(function (root) {
  "use strict";
  const ACRONYMS = new Set(["NHS", "UK", "PLC", "LLP", "CIC", "CIO", "LP", "GB", "IT", "ICT", "BT", "HM", "HMRC", "DVLA", "BBC", "UCL", "EU", "USA",
    "IBM", "KPMG", "PWC", "EY", "ESPO", "YPO", "NHSBSA", "CCS", "UKRI", "TFL", "NI", "ID", "AI", "IP", "HR", "RBS", "UBS"]);
  const SMALL = new Set(["of", "and", "the", "for", "on", "in", "at", "to", "upon", "with", "by", "a"]);

  function capitalise(word) {
    return word.charAt(0).toUpperCase() + word.slice(1).toLowerCase();
  }

  function titleWord(word, isFirst) {
    const bare = word.replace(/[^A-Za-z0-9]/g, "");
    if (!bare) return word;
    if (/\d/.test(bare)) return word;                       // "2B", "A1M", "5G" stay as they are
    if (ACRONYMS.has(bare.toUpperCase())) return word.toUpperCase();
    if (word.toUpperCase() === "LTD" || word.toUpperCase() === "LTD.") return "Ltd" + (word.endsWith(".") ? "." : "");
    if (word === "&") return word;
    if (!isFirst && SMALL.has(bare.toLowerCase())) return word.toLowerCase();
    // hyphenated and apostrophe'd parts: "NEWCASTLE-UPON-TYNE" -> "Newcastle-upon-Tyne", "MARY'S" -> "Mary's"
    return word.split("-").map((part, i) => {
      const b = part.replace(/[^A-Za-z]/g, "").toLowerCase();
      if (i > 0 && SMALL.has(b)) return part.toLowerCase();
      return capitalise(part);
    }).join("-");
  }

  function display(name) {
    if (name == null) return "";
    let s = String(name).replace(/\s+/g, " ").trim().replace(/[:;,]+$/, "").trim();
    if (!s) return "";
    const letters = s.replace(/[^A-Za-z]/g, "");
    const allCaps = letters.length >= 4 && letters === letters.toUpperCase();
    const words = s.split(" ");
    if (allCaps) return words.map((w, i) => titleWord(w, i === 0)).join(" ");
    // mixed case: only the small words in the middle ("London Borough Of Camden" -> "London Borough of Camden")
    return words.map((w, i) => {
      if (i === 0) return w;
      const bare = w.replace(/[^A-Za-z]/g, "");
      return bare && SMALL.has(bare.toLowerCase()) && bare !== bare.toLowerCase() && w === capitalise(w) ? w.toLowerCase() : w;
    }).join(" ");
  }

  const api = { display };
  root.NameFormat = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
