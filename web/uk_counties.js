/**
 * Static UK & Ireland Counties & Council Areas List
 * Complete reference list across Ireland, England, Scotland, Wales, and Northern Ireland.
 * Includes official Unitary Authorities, Scottish Council Areas, and Alias-aware matching.
 */
(function () {
  const UK_COUNTIES = [
    // Ireland (26 Counties)
    { name: "Carlow", region: "Ireland" },
    { name: "Cavan", region: "Ireland" },
    { name: "Clare", region: "Ireland" },
    { name: "Cork", region: "Ireland" },
    { name: "Donegal", region: "Ireland" },
    { name: "Dublin", region: "Ireland" },
    { name: "Galway", region: "Ireland" },
    { name: "Kerry", region: "Ireland" },
    { name: "Kildare", region: "Ireland" },
    { name: "Kilkenny", region: "Ireland" },
    { name: "Laois", region: "Ireland" },
    { name: "Leitrim", region: "Ireland" },
    { name: "Limerick", region: "Ireland" },
    { name: "Longford", region: "Ireland" },
    { name: "Louth", region: "Ireland" },
    { name: "Mayo", region: "Ireland" },
    { name: "Meath", region: "Ireland" },
    { name: "Monaghan", region: "Ireland" },
    { name: "Offaly", region: "Ireland" },
    { name: "Roscommon", region: "Ireland" },
    { name: "Sligo", region: "Ireland" },
    { name: "Tipperary", region: "Ireland" },
    { name: "Waterford", region: "Ireland" },
    { name: "Westmeath", region: "Ireland" },
    { name: "Wexford", region: "Ireland" },
    { name: "Wicklow", region: "Ireland" },

    // England (48 Ceremonial Counties & Major Metropolitan Areas)
    { name: "Bedfordshire", region: "England" },
    { name: "Berkshire", region: "England" },
    { name: "Bristol", region: "England" },
    { name: "Buckinghamshire", region: "England" },
    { name: "Cambridgeshire", region: "England" },
    { name: "Cheshire", region: "England" },
    { name: "City of London", region: "England" },
    { name: "Cornwall", region: "England" },
    { name: "Cumbria", region: "England" },
    { name: "Derbyshire", region: "England" },
    { name: "Devon", region: "England" },
    { name: "Dorset", region: "England" },
    { name: "Durham", region: "England" },
    { name: "East Riding of Yorkshire", region: "England" },
    { name: "East Sussex", region: "England" },
    { name: "Essex", region: "England" },
    { name: "Gloucestershire", region: "England" },
    { name: "Greater London", region: "England" },
    { name: "Greater Manchester", region: "England" },
    { name: "Hampshire", region: "England" },
    { name: "Herefordshire", region: "England" },
    { name: "Hertfordshire", region: "England" },
    { name: "Isle of Wight", region: "England" },
    { name: "Kent", region: "England" },
    { name: "Lancashire", region: "England" },
    { name: "Leicestershire", region: "England" },
    { name: "Lincolnshire", region: "England" },
    { name: "Merseyside", region: "England" },
    { name: "Norfolk", region: "England" },
    { name: "North Yorkshire", region: "England" },
    { name: "Northamptonshire", region: "England" },
    { name: "Northumberland", region: "England" },
    { name: "Nottinghamshire", region: "England" },
    { name: "Oxfordshire", region: "England" },
    { name: "Rutland", region: "England" },
    { name: "Shropshire", region: "England" },
    { name: "Somerset", region: "England" },
    { name: "South Yorkshire", region: "England" },
    { name: "Staffordshire", region: "England" },
    { name: "Suffolk", region: "England" },
    { name: "Surrey", region: "England" },
    { name: "Tyne and Wear", region: "England" },
    { name: "Warwickshire", region: "England" },
    { name: "West Midlands", region: "England" },
    { name: "West Sussex", region: "England" },
    { name: "West Yorkshire", region: "England" },
    { name: "Wiltshire", region: "England" },
    { name: "Worcestershire", region: "England" },

    // Scotland (32 Council Areas)
    { name: "Aberdeen City", region: "Scotland" },
    { name: "Aberdeenshire", region: "Scotland" },
    { name: "Angus", region: "Scotland" },
    { name: "Argyll and Bute", region: "Scotland" },
    { name: "City of Edinburgh", region: "Scotland" },
    { name: "Clackmannanshire", region: "Scotland" },
    { name: "Dumfries and Galloway", region: "Scotland" },
    { name: "Dundee City", region: "Scotland" },
    { name: "East Ayrshire", region: "Scotland" },
    { name: "East Dunbartonshire", region: "Scotland" },
    { name: "East Lothian", region: "Scotland" },
    { name: "East Renfrewshire", region: "Scotland" },
    { name: "Falkirk", region: "Scotland" },
    { name: "Fife", region: "Scotland" },
    { name: "Glasgow City", region: "Scotland" },
    { name: "Highland", region: "Scotland" },
    { name: "Inverclyde", region: "Scotland" },
    { name: "Midlothian", region: "Scotland" },
    { name: "Moray", region: "Scotland" },
    { name: "Na h-Eileanan Siar (Western Isles)", region: "Scotland" },
    { name: "North Ayrshire", region: "Scotland" },
    { name: "North Lanarkshire", region: "Scotland" },
    { name: "Orkney Islands", region: "Scotland" },
    { name: "Perth and Kinross", region: "Scotland" },
    { name: "Renfrewshire", region: "Scotland" },
    { name: "Scottish Borders", region: "Scotland" },
    { name: "Shetland Islands", region: "Scotland" },
    { name: "South Ayrshire", region: "Scotland" },
    { name: "South Lanarkshire", region: "Scotland" },
    { name: "Stirling", region: "Scotland" },
    { name: "West Dunbartonshire", region: "Scotland" },
    { name: "West Lothian", region: "Scotland" },

    // Wales (22 Unitary Authorities + Preserved Counties)
    { name: "Blaenau Gwent", region: "Wales" },
    { name: "Bridgend", region: "Wales" },
    { name: "Caerphilly", region: "Wales" },
    { name: "Cardiff", region: "Wales" },
    { name: "Carmarthenshire", region: "Wales" },
    { name: "Ceredigion", region: "Wales" },
    { name: "Clwyd", region: "Wales" },
    { name: "Conwy", region: "Wales" },
    { name: "Denbighshire", region: "Wales" },
    { name: "Dyfed", region: "Wales" },
    { name: "Flintshire", region: "Wales" },
    { name: "Gwent", region: "Wales" },
    { name: "Gwynedd", region: "Wales" },
    { name: "Isle of Anglesey", region: "Wales" },
    { name: "Merthyr Tydfil", region: "Wales" },
    { name: "Mid Glamorgan", region: "Wales" },
    { name: "Monmouthshire", region: "Wales" },
    { name: "Neath Port Talbot", region: "Wales" },
    { name: "Newport", region: "Wales" },
    { name: "Pembrokeshire", region: "Wales" },
    { name: "Powys", region: "Wales" },
    { name: "Rhondda Cynon Taf", region: "Wales" },
    { name: "South Glamorgan", region: "Wales" },
    { name: "Swansea", region: "Wales" },
    { name: "Torfaen", region: "Wales" },
    { name: "Vale of Glamorgan", region: "Wales" },
    { name: "West Glamorgan", region: "Wales" },
    { name: "Wrexham", region: "Wales" },

    // Northern Ireland (6 Counties & Principal Areas)
    { name: "Antrim", region: "Northern Ireland" },
    { name: "Armagh", region: "Northern Ireland" },
    { name: "Down", region: "Northern Ireland" },
    { name: "Fermanagh", region: "Northern Ireland" },
    { name: "Londonderry", region: "Northern Ireland" },
    { name: "Tyrone", region: "Northern Ireland" }
  ];

  const COUNTY_ALIASES = {
    // England
    "Greater London": ["london", "camden", "greenwich", "hackney", "hammersmith", "fulham", "islington", "kensington", "chelsea", "lambeth", "lewisham", "southwark", "tower hamlets", "wandsworth", "westminster", "barking", "dagenham", "barnet", "bexley", "brent", "bromley", "croydon", "ealing", "enfield", "haringey", "harrow", "havering", "hillingdon", "hounslow", "kingston", "merton", "newham", "redbridge", "richmond", "sutton", "waltham forest", "city of london"],
    "City of London": ["city of london"],
    "Greater Manchester": ["manchester", "salford", "bolton", "bury", "oldham", "rochdale", "stockport", "tameside", "trafford", "wigan"],
    "West Midlands": ["birmingham", "coventry", "wolverhampton", "dudley", "sandwell", "solihull", "walsall"],
    "West Yorkshire": ["leeds", "bradford", "wakefield", "calderdale", "kirklees", "halifax", "huddersfield"],
    "South Yorkshire": ["sheffield", "doncaster", "rotherham", "barnsley"],
    "Merseyside": ["liverpool", "wirral", "sefton", "knowsley", "st helens"],
    "Tyne and Wear": ["newcastle", "sunderland", "gateshead", "south tyneside", "north tyneside"],
    "Bristol": ["bristol", "bath and north east somerset", "north somerset", "south gloucestershire"],
    "East Riding of Yorkshire": ["hull", "kingston upon hull", "east riding"],
    "Berkshire": ["reading", "slough", "bracknell", "windsor", "maidenhead", "wokingham", "west berkshire"],
    "Cambridgeshire": ["cambridge", "peterborough", "fenland", "huntingdonshire"],
    "Cheshire": ["cheshire east", "cheshire west", "chester", "warrington", "halton"],
    "Cornwall": ["cornwall", "isles of scilly", "truro"],
    "Cumbria": ["cumbria", "cumberland", "westmorland", "furness", "carlisle"],
    "Derbyshire": ["derby", "derbyshire"],
    "Devon": ["plymouth", "torbay", "exeter", "devon"],
    "Dorset": ["bournemouth", "poole", "christchurch", "dorset"],
    "Durham": ["county durham", "darlington", "hartlepool", "stockton"],
    "Essex": ["southend", "thurrock", "chelmsford", "colchester", "essex"],
    "Gloucestershire": ["gloucester", "cheltenham", "gloucestershire"],
    "Hampshire": ["southampton", "portsmouth", "winchester", "hampshire"],
    "Lancashire": ["blackpool", "blackburn", "preston", "lancashire", "lancaster"],
    "Leicestershire": ["leicester", "leicestershire"],
    "Lincolnshire": ["lincoln", "north lincolnshire", "north east lincolnshire", "lincolnshire"],
    "Norfolk": ["norwich", "norfolk"],
    "North Yorkshire": ["york", "middlesbrough", "redcar", "north yorkshire", "harrogate"],
    "Northamptonshire": ["northampton", "north northamptonshire", "west northamptonshire"],
    "Nottinghamshire": ["nottingham", "nottinghamshire"],
    "Oxfordshire": ["oxford", "oxfordshire"],
    "Staffordshire": ["stoke", "stoke-on-trent", "staffordshire", "stafford"],
    "Suffolk": ["ipswich", "suffolk"],
    "Surrey": ["surrey", "guildford", "woking", "epsom"],
    "Warwickshire": ["warwick", "warwickshire", "stratford", "nuneaton"],
    "Wiltshire": ["swindon", "salisbury", "wiltshire"],

    // Scotland
    "City of Edinburgh": ["edinburgh", "city of edinburgh"],
    "Glasgow City": ["glasgow", "glasgow city"],
    "Aberdeen City": ["aberdeen", "aberdeen city"],
    "Dundee City": ["dundee", "dundee city"],
    "Highland": ["highlands", "highland", "inverness"],
    "Na h-Eileanan Siar (Western Isles)": ["western isles", "eilean siar", "stornoway", "outer hebrides"],
    "Dumfries and Galloway": ["dumfries", "galloway"],
    "Perth and Kinross": ["perth", "kinross"],
    "Argyll and Bute": ["argyll", "bute"],
    "Scottish Borders": ["borders", "scottish borders"],
    "Orkney Islands": ["orkney"],
    "Shetland Islands": ["shetland"],
    "Fife": ["fife", "kirkcaldy", "dunfermline"],
    "Moray": ["moray", "elgin"],
    "Stirling": ["stirling"],
    "Falkirk": ["falkirk"],

    // Wales
    "Cardiff": ["cardiff", "caerdydd"],
    "Swansea": ["swansea", "abertawe"],
    "Newport": ["newport", "casnewydd"],
    "Rhondda Cynon Taf": ["rhondda", "cynon", "taf", "rct"],
    "Carmarthenshire": ["carmarthen", "carmarthenshire", "sir gâr"],
    "Caerphilly": ["caerphilly", "caerffili"],
    "Flintshire": ["flintshire", "sir y fflint"],
    "Bridgend": ["bridgend", "pen-y-bont"],
    "Neath Port Talbot": ["neath", "port talbot", "castell-nedd"],
    "Wrexham": ["wrexham", "wrecsam"],
    "Powys": ["powys"],
    "Vale of Glamorgan": ["vale of glamorgan", "bro morgannwg", "barry"],
    "Pembrokeshire": ["pembrokeshire", "sir benfro"],
    "Gwynedd": ["gwynedd", "bangor"],
    "Conwy": ["conwy"],
    "Denbighshire": ["denbighshire", "sir ddinbych"],
    "Monmouthshire": ["monmouthshire", "sir fynwy"],
    "Torfaen": ["torfaen"],
    "Blaenau Gwent": ["blaenau gwent"],
    "Ceredigion": ["ceredigion", "aberystwyth"],
    "Isle of Anglesey": ["anglesey", "ynys môn"],
    "Merthyr Tydfil": ["merthyr", "merthyr tydfil"],
    "Clwyd": ["clwyd"],
    "Dyfed": ["dyfed"],
    "Gwent": ["gwent"],
    "Mid Glamorgan": ["mid glamorgan"],
    "South Glamorgan": ["south glamorgan"],
    "West Glamorgan": ["west glamorgan"],

    // Northern Ireland
    "Antrim": ["antrim", "belfast", "lisburn", "ballymena"],
    "Armagh": ["armagh", "craigavon"],
    "Down": ["down", "newry", "mourne", "ards", "north down"],
    "Fermanagh": ["fermanagh", "omagh"],
    "Londonderry": ["londonderry", "derry", "strabane", "causeway"],
    "Tyrone": ["tyrone", "dungannon"],

    // Ireland
    "Dublin": ["dublin", "dlr", "fingal", "south dublin"],
    "Cork": ["cork"],
    "Galway": ["galway"],
    "Limerick": ["limerick"],
    "Waterford": ["waterford"],
    "Tipperary": ["tipperary"],
    "Kildare": ["kildare"],
    "Meath": ["meath"],
    "Wicklow": ["wicklow"],
    "Louth": ["louth", "drogheda", "dundalk"],
    "Donegal": ["donegal"],
    "Kerry": ["kerry", "tralee", "killarney"],
    "Mayo": ["mayo", "castlebar"],
    "Clare": ["clare", "ennis"],
    "Wexford": ["wexford"],
    "Kilkenny": ["kilkenny"],
    "Westmeath": ["westmeath", "athlon", "mullingar"],
    "Laois": ["laois", "portlaoise"],
    "Offaly": ["offaly", "tullamore"],
    "Cavan": ["cavan"],
    "Sligo": ["sligo"],
    "Roscommon": ["roscommon"],
    "Monaghan": ["monaghan"],
    "Carlow": ["carlow"],
    "Longford": ["longford"],
    "Leitrim": ["leitrim", "carrick-on-shannon"]
  };

  const SPECIAL_WORD_PATTERNS = {
    "down": "\\bcounty\\s+down\\b|\\bco\\.?\\s*down\\b|\\bdown\\s+(district|council|area)\\b|\\bnewry.*down\\b",
    "mayo": "\\bcounty\\s+mayo\\b|\\bco\\.?\\s*mayo\\b|\\bmayo\\b(?!r)",
    "clare": "\\bcounty\\s+clare\\b|\\bco\\.?\\s*clare\\b|\\bclare\\b(?![\\w])",
    "ross": "\\bross\\b|\\bross-shire\\b"
  };

  function escapeRegex(str) {
    return str.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  const _regexCache = new Map();

  function getCountyRegex(countyName) {
    if (!countyName) return null;
    const key = countyName.toLowerCase().trim();
    if (_regexCache.has(key)) {
      return _regexCache.get(key);
    }

    const terms = [countyName];
    if (COUNTY_ALIASES[countyName]) {
      terms.push(...COUNTY_ALIASES[countyName]);
    } else {
      for (const [k, aliases] of Object.entries(COUNTY_ALIASES)) {
        if (k.toLowerCase() === key) {
          terms.push(...aliases);
          break;
        }
      }
    }

    const parts = [];
    for (const term of terms) {
      const t = term.toLowerCase().trim();
      if (SPECIAL_WORD_PATTERNS[t]) {
        parts.push(SPECIAL_WORD_PATTERNS[t]);
      } else if (t.length <= 4) {
        parts.push(`\\b${escapeRegex(t)}\\b`);
      } else {
        parts.push(`\\b${escapeRegex(t)}`);
      }
    }

    try {
      const re = new RegExp(parts.join("|"), "i");
      _regexCache.set(key, re);
      return re;
    } catch (e) {
      console.warn("Regex compile error for county:", countyName, e);
      return null;
    }
  }

  // Portals whose notices are UK / Ireland tenders -- the only ones a county can place. A portal
  // missing from this list has ALL of its rows hidden whenever a county is selected (ProContract
  // and GCA did exactly that in an England-only search). Keep in step with UK_IE_SOURCE_IDS in
  // etenders_scraper/sources/registry.py and REGION_PORTAL_IDS in app.js.
  const UK_IE_SOURCE_IDS = new Set([
    "etenders_ie", "etenders_ni", "sell2wales", "pcs",
    "find_tender", "contracts_finder", "procontract", "gca_agreements",
  ]);

  // A notice's own location often names only a nation or region, never a county: Find a Tender writes
  // "UKD - North West (England)", "UKI - London", "UKM - Scotland". Text matching on county names cannot
  // place those, so an England-only search lost every one of them (Round 27). A notice whose LOCATION
  // names a nation is in the selection when EVERY county of that nation is selected (picking one county
  // does not bring in the whole nation). Only the location and region fields are read for this, not the
  // title or description, where "London" or "North West" can mean anything.
  const NATION_HINTS = {
    "England": /\b(england|yorkshire and the humber|east of england|north east|north west|south east|south west|east midlands|west midlands|london)\b/i,
    "Scotland": /\bscotland\b/i,
    "Wales": /\bwales\b/i,
    "Northern Ireland": /\bnorthern ireland\b/i,
  };

  // The nations (England, Scotland, Wales, Northern Ireland) every one of whose counties is selected.
  function fullySelectedNations(selectedCounties) {
    const selected = new Set(Array.isArray(selectedCounties) ? selectedCounties : []);
    return Object.keys(NATION_HINTS).filter((nation) => {
      const counties = UK_COUNTIES.filter((c) => c.region === nation);
      return counties.length > 0 && counties.every((c) => selected.has(c.name));
    });
  }

  function isLocationUnstated(row) {
    if (!row) return true;
    const loc = (row.location || "").trim().toLowerCase();
    const reg = (row.region || "").trim().toLowerCase();
    const combined = `${loc} ${reg}`.trim();
    if (!combined) return true;
    if (["location not stated", "not stated", "unspecified", "location not specified", "not specified", "n/a", "uk", "united kingdom", "unknown"].includes(combined)) return true;
    return false;
  }

  function matchRowToCounties(row, selectedCounties) {
    if (!row || !Array.isArray(selectedCounties) || selectedCounties.length === 0 || selectedCounties.length >= UK_COUNTIES.length) {
      return true;
    }

    // Check if row is from a UK/IE source
    const source = (row.source || "").toLowerCase();
    const isUkOrIeSource = UK_IE_SOURCE_IDS.has(source);

    // If row is from a completely non-UK/IE international portal (e.g. boamp, bund), don't exclude it unless UK/IE only is expected
    if (!isUkOrIeSource && source) {
      return false; // County filter specifically filters to selected counties
    }

    const textToSearch = [
      row.title || "",
      row.contracting_authority || "",
      row.location || "",
      row.description || "",
      row.region || ""
    ].join(" ");

    for (const county of selectedCounties) {
      const re = getCountyRegex(county);
      if (re && re.test(textToSearch)) {
        return true;
      }
    }

    const where = `${row.location || ""} ${row.region || ""}`;
    if (where.trim()) {
      for (const nation of fullySelectedNations(selectedCounties)) {
        if (NATION_HINTS[nation].test(where)) return true;
      }
    }

    if (isLocationUnstated(row)) {
      row.location_not_stated = true;
      return true;
    }

    return false;
  }

  window.UK_COUNTIES = UK_COUNTIES;
  window.fullySelectedNations = fullySelectedNations;
  window.COUNTY_ALIASES = COUNTY_ALIASES;
  window.getCountyRegex = getCountyRegex;
  window.matchRowToCounties = matchRowToCounties;
  window.isLocationUnstated = isLocationUnstated;
})();
