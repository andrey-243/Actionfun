"""
Aliases & acronymes par ticker — complément à yfinance shortName/longName.
Couvre les cas où le ticker ou le nom officiel ne correspond pas aux headlines :
  - Noms de marque (Meta/Facebook, Alphabet/Google)
  - Acronymes journalistiques (BofA, JPM→JPMorgan)
  - Filiales connues (LVMH→Dior, Toyota→Lexus)
  - Noms locaux ASIA (7203.T→Toyota)
"""

TICKER_ALIASES: dict[str, list[str]] = {

    # ─── US — Mega cap tech ───────────────────────────────────────────────────
    "AAPL":   ["Apple", "iPhone", "iPad", "Mac", "App Store", "Tim Cook"],
    "MSFT":   ["Microsoft", "Azure", "Windows", "Xbox", "LinkedIn", "GitHub", "Satya Nadella"],
    "NVDA":   ["Nvidia", "CUDA", "GeForce", "Jensen Huang"],
    "AMZN":   [
        "Amazon", "AWS", "Amazon Web Services",
        # removed: "Prime" — adjectif courant (prime rate, prime minister)
        "Jeff Bezos", "Andy Jassy",
    ],
    "GOOGL":  [
        "Alphabet", "Google", "YouTube", "DeepMind", "Waymo",
        # removed: "Chrome" — aussi un matériau/finish, terme générique de navigateur
        # removed: "Android" — plateforme citée dans articles sur Samsung, Xiaomi, etc.
        "Sundar Pichai",
    ],
    "GOOG":   ["Alphabet", "Google", "YouTube", "DeepMind", "Waymo"],
    "META":   [
        "Facebook",
        # removed: "FB" — 2 lettres, acronyme ambigu
        "Meta Platforms", "Instagram", "WhatsApp",
        # removed: "Threads" — mot commun (fils de discussion)
        "Mark Zuckerberg",
    ],
    "TSLA":   ["Tesla", "Model 3", "Model S", "Model Y", "Elon Musk", "Powerwall"],
    "AVGO":   ["Broadcom"],
    "ORCL":   ["Oracle", "Oracle Cloud"],
    "AMD":    ["Advanced Micro Devices", "Ryzen", "EPYC", "Radeon"],
    "QCOM":   ["Qualcomm", "Snapdragon"],
    "TXN":    ["Texas Instruments"],
    "AMAT":   ["Applied Materials"],
    "LRCX":   ["Lam Research"],
    "KLAC":   ["KLA Corporation"],
    "MCHP":   ["Microchip Technology"],
    "SNPS":   ["Synopsys"],
    "CDNS":   ["Cadence"],
    "ADI":    ["Analog Devices"],
    "MU":     ["Micron", "Micron Technology"],
    "INTC":   [
        "Intel",
        # removed: "Core" — mot générique, processeurs AMD s'appellent aussi "core"
        "Xeon",
    ],
    "ARM":    [
        "Arm Holdings",
        # removed: "Arm" — mot commun anglais (bras)
    ],
    "CSCO":   ["Cisco", "Cisco Systems", "Webex", "Meraki"],
    # removed from CSCO: "Splunk" — était indépendant avant acquisition 2024, encore cité seul
    "PANW":   [
        "Palo Alto Networks",
        # removed: "Palo Alto" — ville en Californie, apparaît dans des milliers d'articles tech
    ],
    "CRWD":   ["CrowdStrike"],
    "FTNT":   ["Fortinet"],
    "ZS":     ["Zscaler"],
    "NET":    ["Cloudflare"],
    "OKTA":   ["Okta"],
    "DDOG":   ["Datadog"],
    "SNOW":   ["Snowflake"],
    "NOW":    ["ServiceNow"],
    "CRM":    ["Salesforce", "Slack"],
    "INTU":   ["Intuit", "TurboTax", "QuickBooks", "Credit Karma"],
    "ADBE":   ["Adobe", "Photoshop", "Acrobat", "Figma"],
    "WDAY":   ["Workday"],
    "TEAM":   ["Atlassian", "Jira", "Confluence", "Trello"],
    "MDB":    ["MongoDB"],
    "HUBS":   ["HubSpot"],
    "VEEV":   ["Veeva"],

    # ─── US — Finance ─────────────────────────────────────────────────────────
    "JPM":    ["JPMorgan", "JP Morgan", "J.P. Morgan", "JPMorgan Chase", "Jamie Dimon"],
    "BAC":    [
        "Bank of America", "BofA", "Merrill Lynch",
        # removed: "Merrill" — prénom/patronyme très courant
    ],
    "WFC":    ["Wells Fargo"],
    "GS":     ["Goldman Sachs", "Goldman"],
    "MS":     ["Morgan Stanley"],
    "C":      ["Citigroup", "Citi", "Citibank", "Citicorp"],
    "USB":    ["U.S. Bancorp", "US Bank"],
    "PNC":    ["PNC Financial", "PNC Bank"],
    "TFC":    ["Truist"],
    "COF":    ["Capital One"],
    "AXP":    ["American Express", "Amex"],
    "V":      ["Visa"],
    "MA":     ["Mastercard"],
    "PYPL":   ["PayPal", "Venmo"],
    "XYZ":    [
        # removed: "Block" — mot extrêmement générique (blockchain, city block, etc.)
        # removed: "Square" — forme géométrique, terme financier générique
        "Cash App", "CashApp", "Afterpay",
    ],
    "COIN":   ["Coinbase"],
    "SCHW":   ["Charles Schwab", "Schwab"],
    "BLK":    ["BlackRock"],
    "BX":     ["Blackstone"],
    "KKR":    ["KKR & Co", "KKR"],
    "APO":    ["Apollo Global", "Apollo"],
    "ARES":   ["Ares Management", "Ares"],
    "ICE":    ["Intercontinental Exchange"],
    "CME":    ["CME Group", "Chicago Mercantile Exchange"],
    "NDAQ":   ["Nasdaq"],
    "SPGI":   ["S&P Global", "SP Global"],
    "MCO":    ["Moody's"],
    "MSCI":   ["MSCI"],
    "BR":     ["Broadridge"],

    # ─── US — Santé ───────────────────────────────────────────────────────────
    "LLY":    [
        "Eli Lilly",
        # removed: "Lilly" — patronyme courant (Lilly Pulitzer, etc.)
        "Mounjaro", "Zepbound", "Tirzepatide",
    ],
    "UNH":    ["UnitedHealth", "United Health", "UnitedHealthcare", "Optum"],
    "JNJ":    ["Johnson & Johnson", "J&J", "Janssen", "Kenvue"],
    "ABBV":   ["AbbVie", "Humira", "Skyrizi", "Rinvoq"],
    "MRK":    ["Merck", "Keytruda"],
    "PFE":    [
        "Pfizer",
        # removed: "BioNTech" — société cotée indépendamment (BNTX), crée des FP croisés
    ],
    "BMY":    [
        "Bristol Myers Squibb", "Bristol-Myers Squibb",
        # removed: "BMS" — acronyme partagé (Bayerische Motoren... BMS Energy, etc.)
    ],
    "AMGN":   ["Amgen"],
    "GILD":   ["Gilead", "Gilead Sciences"],
    "REGN":   ["Regeneron"],
    "VRTX":   ["Vertex"],
    "BIIB":   ["Biogen"],
    "ISRG":   ["Intuitive Surgical", "da Vinci"],
    "MDT":    ["Medtronic"],
    "ABT":    ["Abbott", "Abbott Laboratories"],
    "DHR":    ["Danaher"],
    "TMO":    ["Thermo Fisher", "Thermo Fisher Scientific"],
    "IQV":    ["IQVIA"],
    "ZTS":    ["Zoetis"],
    "EW":     ["Edwards Lifesciences"],
    "SYK":    ["Stryker"],
    "BSX":    ["Boston Scientific"],

    # ─── US — Conso ───────────────────────────────────────────────────────────
    "WMT":    ["Walmart", "Sam's Club"],
    "COST":   ["Costco"],
    "TGT":    ["Target"],
    "HD":     ["Home Depot"],
    "LOW":    ["Lowe's", "Lowes"],
    "TJX":    ["TJ Maxx", "Marshalls", "HomeGoods"],
    "ROST":   ["Ross Stores", "Ross"],
    "MCD":    ["McDonald's", "McDonalds"],
    "SBUX":   ["Starbucks"],
    "CMG":    ["Chipotle"],
    "YUM":    ["Yum Brands", "KFC", "Pizza Hut", "Taco Bell"],
    "DPZ":    ["Domino's", "Dominos"],
    "QSR":    ["Restaurant Brands", "Burger King", "Tim Hortons", "Popeyes"],
    "PG":     [
        "Procter & Gamble", "P&G",
        # removed: "Tide" — marée en anglais, apparaît dans des contextes météo/finance
        "Gillette", "Pampers",
    ],
    "KO":     [
        "Coca-Cola", "Coca Cola",
        # removed: "Coke" — ambigu (cocaïne, charbon de coke)
        # removed: "Sprite" — mot commun (lutin, elfe)
        "Fanta",
    ],
    "PEP":    ["PepsiCo", "Pepsi", "Frito-Lay", "Gatorade", "Lay's"],
    "PM":     ["Philip Morris", "Philip Morris International"],
    "MO":     ["Altria", "Marlboro"],
    "MDLZ":   ["Mondelez", "Oreo", "Cadbury", "Milka"],
    "GIS":    ["General Mills", "Cheerios", "Betty Crocker"],
    "CL":     ["Colgate-Palmolive", "Colgate"],
    "EL":     [
        "Estée Lauder", "Estee Lauder",
        # removed: "MAC" — ambigu (Apple Mac, adresse MAC réseau, cosmétiques MAC sont filiale mais FP élevés)
        "Clinique",
    ],

    # ─── US — Energie ─────────────────────────────────────────────────────────
    "XOM":    ["ExxonMobil", "Exxon", "Mobil"],
    "CVX":    ["Chevron"],
    "COP":    ["ConocoPhillips", "Conoco"],
    "EOG":    ["EOG Resources"],
    "HAL":    ["Halliburton"],
    "SLB":    ["Schlumberger", "SLB"],

    # ─── US — Industriels ─────────────────────────────────────────────────────
    "GE":     ["General Electric", "GE Aerospace", "GE Vernova"],
    "BA":     ["Boeing"],
    "RTX":    ["Raytheon", "Pratt & Whitney", "Collins Aerospace"],
    "LMT":    ["Lockheed Martin", "Lockheed", "F-35"],
    "NOC":    ["Northrop Grumman", "Northrop"],
    "GD":     ["General Dynamics", "Gulfstream"],
    "CAT":    ["Caterpillar"],
    "DE":     ["John Deere", "Deere"],
    "HON":    ["Honeywell"],
    "ETN":    ["Eaton"],
    "UPS":    ["UPS", "United Parcel Service"],
    "FDX":    ["FedEx", "Federal Express"],
    "UNP":    ["Union Pacific"],
    "NSC":    ["Norfolk Southern"],
    "ODFL":   ["Old Dominion Freight"],

    # ─── US — Media / Telecom / Entertainment ─────────────────────────────────
    "NFLX":   ["Netflix"],
    "DIS":    [
        "Disney", "Walt Disney", "Disney+", "Hulu", "ESPN", "Pixar",
        # removed: "Marvel" — présence culturelle large indépendante du titre Disney
        "Star Wars",
    ],
    "CMCSA":  [
        "Comcast", "NBCUniversal",
        # removed: "NBC" — chaîne citée seule dans contextes media sans lien Comcast
        "Universal Pictures",
        # removed: "Peacock" — mot commun (paon), plateforme peu citée seule
    ],
    "WBD":    [
        "Warner Bros", "Warner Brothers", "HBO", "CNN",
        # removed: "Discovery" — mot courant (découverte scientifique, NASA Discovery, etc.)
    ],
    "FOXA":   ["Fox News", "Fox Corporation"],
    "NYT":    ["New York Times"],
    "SPOT":   ["Spotify"],
    "RBLX":   ["Roblox"],
    "BKNG":   [
        "Booking.com", "Booking Holdings", "Priceline",
        # removed: "Kayak" — sport nautique, terme générique
        "OpenTable",
    ],
    "ABNB":   ["Airbnb"],
    "UBER":   ["Uber", "Uber Eats"],
    "LYFT":   ["Lyft"],
    "DASH":   ["DoorDash"],
    "EXPE":   ["Expedia", "Hotels.com", "Vrbo", "Trivago"],
    "TMUS":   ["T-Mobile"],
    "VZ":     ["Verizon"],
    "T":      ["AT&T"],
    "CHTR":   [
        "Charter Communications",
        # removed: "Spectrum" — terme physique générique (spectre lumineux, spectre politique)
    ],

    # ─── US — Immobilier / Utilities ──────────────────────────────────────────
    "NEE":    ["NextEra Energy", "NextEra"],
    "AMT":    ["American Tower"],
    "PLD":    ["Prologis"],
    "EQIX":   ["Equinix"],
    "DLR":    ["Digital Realty"],
    "SPG":    [
        "Simon Property Group",
        # removed: "Simon" — prénom extrêmement courant
    ],

    # ─── US — Matériaux / Mines ───────────────────────────────────────────────
    "NEM":    ["Newmont", "Newmont Mining"],
    "FCX":    ["Freeport-McMoRan", "Freeport", "Freeport McMoRan"],
    "LIN":    ["Linde"],
    "APD":    ["Air Products"],
    "DOW":    ["Dow Inc", "Dow Chemical"],
    "DD":     ["DuPont"],
    "SHW":    ["Sherwin-Williams"],

    # ─── US — Gaming ──────────────────────────────────────────────────────────
    "DKNG":   ["DraftKings"],
    "MGM":    ["MGM Resorts", "MGM"],
    "LVS":    [
        "Las Vegas Sands",
        # removed: "Sands" — mot commun (sable, sables mouvants)
    ],
    "U":      ["Unity Technologies", "Unity"],

    # ─── EU — France (.PA) ────────────────────────────────────────────────────
    "MC.PA":   ["LVMH", "Louis Vuitton", "Moët Hennessy", "Dior", "Givenchy", "Bulgari", "TAG Heuer", "Sephora", "Bernard Arnault"],
    "TTE.PA":  [
        "TotalEnergies",
        # removed: "Total" — adjectif courant (total revenue, total assets, etc.)
    ],
    "SAN.PA":  ["Sanofi", "Dupixent"],
    "AIR.PA":  ["Airbus", "A320", "A350"],
    "OR.PA":   ["L'Oréal", "L'Oreal", "Loreal", "Maybelline", "Lancôme", "Garnier"],
    "BNP.PA":  ["BNP Paribas", "BNP"],
    "DG.PA":   ["Vinci"],
    "SU.PA":   [
        "Schneider Electric",
        # removed: "Schneider" — patronyme répandu, de nombreuses autres entreprises
    ],
    "AI.PA":   ["Air Liquide"],
    "RI.PA":   [
        "Pernod Ricard", "Pernod", "Absolut", "Jameson",
        # removed: "Malibu" — ville en Californie très citée dans contextes non-alcool
    ],
    "CAP.PA":  ["Capgemini"],
    "DSY.PA":  ["Dassault Systèmes", "Dassault", "CATIA", "SolidWorks"],
    "VIE.PA":  ["Veolia"],
    "KER.PA":  ["Kering", "Gucci", "Yves Saint Laurent", "YSL", "Balenciaga", "Bottega Veneta", "François-Henri Pinault"],
    "GLE.PA":  ["Société Générale", "SocGen"],
    "ORA.PA":  [
        # removed: "Orange" — couleur et fruit, FP massifs dans tous types d'articles
        "France Telecom",
    ],
    "SAF.PA":  ["Safran"],
    "HO.PA":   ["Thales"],
    "CS.PA":   ["AXA"],
    "EN.PA":   ["Bouygues"],
    "SGO.PA":  ["Saint-Gobain"],
    "CA.PA":   ["Crédit Agricole", "Credit Agricole"],
    "AC.PA":   ["Accor", "Novotel", "Ibis", "Mercure"],
    "RNO.PA":  ["Renault"],
    "PUB.PA":  ["Publicis", "Publicis Groupe"],
    "ENGI.PA": ["Engie", "GDF Suez"],
    "EL.PA":   ["EssilorLuxottica", "Essilor", "Luxottica", "Ray-Ban", "Oakley", "Varilux"],
    "URW.PA":  ["Unibail", "Unibail-Rodamco-Westfield", "Westfield"],

    # ─── EU — Allemagne (.DE) ─────────────────────────────────────────────────
    "SAP.DE":   ["SAP", "SAP SE"],
    "SIE.DE":   ["Siemens"],
    "ALV.DE":   ["Allianz"],
    "DTE.DE":   [
        "Deutsche Telekom",
        # removed: "Telekom" — terme générique allemand pour télécommunications
    ],
    "BAYN.DE":  ["Bayer", "Aspirin"],
    "BMW.DE":   [
        "BMW", "Bayerische Motoren Werke",
        # removed: "MINI" — adjectif commun, FP élevés
        # removed: "Rolls-Royce" — Rolls-Royce Holdings plc (moteurs d'avion) est cotée séparément (RR.L)
    ],
    "MBG.DE":   [
        "Mercedes-Benz", "Mercedes", "Daimler",
        # removed: "AMG" — acronyme partagé (Asset Management Group, etc.)
        # removed: "EQ" — 2 lettres, extrêmement générique
    ],
    "BAS.DE":   ["BASF"],
    "MUV2.DE":  ["Munich Re", "Münchener Rück"],
    "VOW3.DE":  [
        "Volkswagen", "VW", "Audi", "Porsche", "Skoda",
        # removed: "SEAT" — mot courant anglais (siège), FP massifs
        "Lamborghini", "Bentley",
    ],
    "IFX.DE":   ["Infineon", "Infineon Technologies"],
    "ADS.DE":   ["Adidas"],
    "RWE.DE":   ["RWE"],
    "DBK.DE":   ["Deutsche Bank"],
    "CON.DE":   [
        # removed: "Continental" — nom générique (Continental Airlines, petit-déjeuner continental, etc.)
    ],
    "EOAN.DE":  ["E.ON", "EON"],
    "FME.DE":   ["Fresenius Medical Care"],
    "FRE.DE":   ["Fresenius"],
    "HEI.DE":   ["Heidelberg Materials", "HeidelbergCement"],
    "MRK.DE":   ["Merck KGaA"],
    "HEN3.DE":  ["Henkel", "Schwarzkopf", "Persil"],
    "DHL.DE":   ["DHL", "Deutsche Post"],
    "BOSS.DE":  ["Hugo Boss"],
    "DHER.DE":  ["Delivery Hero"],

    # ─── EU — Royaume-Uni (.L) ────────────────────────────────────────────────
    "SHEL.L":   ["Shell", "Royal Dutch Shell"],
    "AZN.L":    [
        "AstraZeneca",
        # removed: "AZ" — abréviation d'Arizona, initiales trop courtes
    ],
    "HSBA.L":   ["HSBC"],
    "ULVR.L":   [
        "Unilever",
        # removed: "Dove" — colombe, mot courant anglais
        # removed: "Axe" — outil commun, FP massifs
        "Hellmann's", "Ben & Jerry's",
    ],
    "BP.L":     ["BP", "British Petroleum"],
    "GSK.L":    ["GlaxoSmithKline", "GSK", "Haleon"],
    "RIO.L":    ["Rio Tinto"],
    "DGE.L":    ["Diageo", "Guinness", "Johnnie Walker", "Smirnoff", "Baileys"],
    "BATS.L":   [
        "British American Tobacco",
        # removed: "BAT" — chauve-souris / batte de cricket en anglais
        "Vuse",
    ],
    "REL.L":    ["RELX", "Elsevier", "LexisNexis"],
    "BHP.L":    ["BHP", "BHP Group"],
    "PRU.L":    ["Prudential"],
    "LSEG.L":   [
        "London Stock Exchange",
        # removed: "LSE" — aussi London School of Economics, ambiguïté forte
        "Refinitiv",
    ],
    "AAL.L":    ["Anglo American"],
    "GLEN.L":   ["Glencore"],
    "NXT.L":    [
        # removed: "Next" — mot extrêmement générique (next quarter, next year, etc.)
    ],
    "BARC.L":   ["Barclays"],
    "LLOY.L":   [
        "Lloyds", "Lloyds Banking Group",
        # removed: "Halifax" — ville (Nouvelle-Écosse et Yorkshire), FP géographiques
    ],
    "RKT.L":    ["Reckitt", "Reckitt Benckiser", "Dettol", "Durex", "Nurofen"],
    "IMB.L":    ["Imperial Brands"],
    "CRH.L":    ["CRH"],
    "EXPN.L":   ["Experian"],
    "FLTR.L":   [
        # removed: "Flutter" — mot commun (battement d'ailes, agitation)
        "Paddy Power", "Betfair", "FanDuel",
    ],
    "NWG.L":    ["NatWest", "Royal Bank of Scotland", "RBS"],
    "STAN.L":   ["Standard Chartered"],
    "VOD.L":    ["Vodafone"],
    "SKG.L":    ["Smurfit Kappa", "Smurfit"],

    # ─── EU — Pays-Bas (.AS) ──────────────────────────────────────────────────
    "ASML.AS":  ["ASML", "EUV lithography"],
    "HEIA.AS":  ["Heineken"],
    "RAND.AS":  ["Randstad"],
    "PHIA.AS":  ["Philips"],
    "ABN.AS":   ["ABN AMRO"],
    "INGA.AS":  ["ING", "ING Group"],
    "MT.AS":    ["ArcelorMittal"],
    "AKZA.AS":  ["Akzo Nobel", "Dulux"],
    "WKL.AS":   ["Wolters Kluwer"],
    "BESI.AS":  ["Besi", "BE Semiconductor"],

    # ─── EU — Suisse (.SW) ────────────────────────────────────────────────────
    "NESN.SW":  ["Nestlé", "Nestle", "Nespresso", "KitKat", "Maggi"],
    "RO.SW":    ["Roche", "Roche Holding", "Genentech"],
    "NOVN.SW":  ["Novartis", "Sandoz"],
    "CFR.SW":   ["Richemont", "Cartier", "IWC", "Van Cleef & Arpels", "Piaget"],
    "UBSG.SW":  ["UBS", "Credit Suisse"],
    "ZURN.SW":  [
        "Zurich Insurance",
        # removed: "Zurich" — ville suisse très citée dans des contextes non-assurance
    ],
    "SREN.SW":  ["Swiss Re"],
    "ABBN.SW":  ["ABB"],
    "GIVN.SW":  ["Givaudan"],
    "SIKA.SW":  ["Sika"],
    "SCMN.SW":  ["Swisscom"],

    # ─── EU — Italie (.MI) ────────────────────────────────────────────────────
    "ENI.MI":   ["Eni"],
    "ENEL.MI":  ["Enel"],
    "ISP.MI":   ["Intesa Sanpaolo", "Intesa"],
    "UCG.MI":   ["UniCredit"],
    "TIT.MI":   [
        "Telecom Italia",
        # removed: "TIM" — prénom courant, abréviation de "time" et d'autres entités
    ],
    "MB.MI":    ["Mediobanca"],
    "RACE.MI":  ["Ferrari", "Scuderia Ferrari"],
    "PRY.MI":   ["Prysmian"],

    # ─── EU — Espagne (.MC) ───────────────────────────────────────────────────
    "SAN.MC":   ["Banco Santander", "Santander"],
    "BBVA.MC":  ["BBVA", "Banco Bilbao Vizcaya"],
    "IBE.MC":   ["Iberdrola"],
    "TEF.MC":   ["Telefónica", "Telefonica", "Movistar"],
    "ITX.MC":   ["Inditex", "Zara", "Massimo Dutti", "Pull&Bear", "Bershka", "Stradivarius"],
    "REP.MC":   ["Repsol"],

    # ─── EU — Scandinavie ─────────────────────────────────────────────────────
    "VOLV-B.ST":   ["Volvo", "Volvo Group", "Volvo Cars"],
    "ERIC-B.ST":   ["Ericsson"],
    "SAND.ST":     ["Sandvik"],
    "NOVO-B.CO":   ["Novo Nordisk", "Ozempic", "Wegovy", "Semaglutide", "Saxenda"],
    "DSV.CO":      ["DSV"],
    "CARL-B.CO":   ["Carlsberg"],
    "ORSTED.CO":   ["Ørsted", "Orsted"],
    "NOKIA.HE":    ["Nokia"],
    "NESTE.HE":    ["Neste"],
    "UPM.HE":      ["UPM"],

    # ─── EU — Benelux / Irlande ───────────────────────────────────────────────
    "ABI.BR":      [
        "AB InBev", "Anheuser-Busch", "Budweiser", "Stella Artois",
        # removed: "Corona" — extrêmement ambigu post-pandémie (COVID-19 + bière citée indépendamment)
        "Leffe", "Hoegaarden",
    ],
    "UCB.BR":      ["UCB"],
    "KBC.BR":      ["KBC Group"],
    "SOLB.BR":     ["Solvay"],
    "GBLB.BR":     ["GBL", "Groupe Bruxelles Lambert"],
    "EDP.LS":      ["EDP", "Energias de Portugal"],
    "GALP.LS":     ["Galp"],

    # ─── ASIA — Japon (.T) ───────────────────────────────────────────────────
    "7203.T":  ["Toyota", "Lexus"],
    "6758.T":  ["Sony", "PlayStation", "PS5", "Sony Pictures", "Sony Music"],
    "9432.T":  ["NTT", "Nippon Telegraph and Telephone"],
    "6861.T":  ["Keyence"],
    "8306.T":  ["Mitsubishi UFJ", "MUFG", "Bank of Tokyo"],
    "9433.T":  [
        "KDDI",
        # removed: "au" — 2 lettres, trop courant (préposition française, "au revoir", etc.)
    ],
    "6902.T":  ["Denso"],
    "4063.T":  ["Shin-Etsu Chemical", "Shin-Etsu"],
    "7974.T":  [
        "Nintendo",
        # removed: "Mario" — prénom très courant en Europe/Amérique latine
        "Zelda",
        # removed: "Switch" — mot courant (interrupteur, changer, Nintendo Switch mais FP élevés)
        "Game Boy",
    ],
    "9984.T":  ["SoftBank", "Softbank Group", "Vision Fund", "Masayoshi Son"],
    "8316.T":  ["Sumitomo Mitsui", "SMBC", "Sumitomo"],
    "4502.T":  ["Takeda", "Takeda Pharmaceutical"],
    "6954.T":  ["Fanuc"],
    "7267.T":  ["Honda", "Acura"],
    "6501.T":  ["Hitachi"],
    "5108.T":  ["Bridgestone"],
    "4661.T":  ["Oriental Land", "Tokyo Disneyland", "DisneySea"],
    "8035.T":  [
        "Tokyo Electron",
        # removed: "TEL" — abréviation universelle de téléphone
    ],
    "9022.T":  ["Central Japan Railway", "JR Central", "Shinkansen"],
    "3382.T":  ["Seven & i Holdings", "7-Eleven"],
    "2914.T":  [
        "Japan Tobacco",
        # removed: "JT" — initiales courantes (Justin Timberlake, journaux, etc.)
    ],
    "6098.T":  ["Recruit Holdings", "Indeed", "Glassdoor"],
    "7270.T":  ["Subaru"],
    "6367.T":  ["Daikin"],
    "8766.T":  ["Tokio Marine"],
    "4568.T":  ["Daiichi Sankyo"],
    "9020.T":  ["East Japan Railway", "JR East"],
    "6971.T":  ["Kyocera"],
    "9613.T":  ["NTT Data"],
    "9434.T":  ["SoftBank Corp"],
    "8411.T":  ["Mizuho Financial", "Mizuho"],
    "7733.T":  ["Olympus"],
    "6594.T":  ["Nidec"],
    "6723.T":  ["Renesas"],
    "4519.T":  ["Chugai Pharmaceutical"],
    "6702.T":  ["Fujitsu"],
    "9107.T":  ["Kawasaki Kisen", "K Line"],
    "5401.T":  ["Nippon Steel"],
    "3659.T":  ["Nexon"],
    "7832.T":  ["Bandai Namco", "Bandai"],
    "6752.T":  ["Panasonic"],
    "4543.T":  ["Terumo"],
    "8058.T":  ["Mitsubishi Corporation"],
    "8001.T":  ["Itochu"],
    "7741.T":  ["HOYA"],
    "4151.T":  ["Astellas Pharma", "Astellas"],
    "7011.T":  ["Mitsubishi Heavy Industries", "MHI"],
    "8802.T":  ["Mitsubishi Estate"],
    "6762.T":  ["TDK"],
    "9735.T":  ["Secom"],

    # ─── ASIA — Chine / Hong Kong (.HK) ──────────────────────────────────────
    "0700.HK":  [
        "Tencent", "WeChat", "Wechat",
        # removed: "QQ" — 2 lettres, trop court
        "WeBank",
    ],
    "0939.HK":  ["CCB", "China Construction Bank"],
    "1299.HK":  ["AIA", "AIA Group"],
    "3988.HK":  [
        "Bank of China",
        # removed: "BOC" — partagé (Bank of Canada, Bank of Commerce, etc.)
    ],
    "0941.HK":  ["China Mobile"],
    "2318.HK":  ["Ping An", "Ping An Insurance"],
    "1398.HK":  ["ICBC", "Industrial and Commercial Bank of China"],
    "0005.HK":  ["HSBC"],
    "9988.HK":  ["Alibaba", "Taobao", "Tmall", "Lazada", "AliExpress", "Cainiao", "Jack Ma"],
    "0388.HK":  ["HKEX", "Hong Kong Stock Exchange"],
    "2628.HK":  ["China Life", "China Life Insurance"],
    "0883.HK":  ["CNOOC"],
    "1211.HK":  ["BYD", "BYD Auto", "BYD Electric"],
    "9618.HK":  [
        "JD.com",
        # removed: "JD" — initiales très communes
    ],
    "0960.HK":  ["Longfor Group", "Longfor"],
    "3690.HK":  ["Meituan"],
    "1024.HK":  ["Kuaishou"],
    "0669.HK":  [
        "Techtronic Industries",
        # removed: "TTI" — acronyme ambigu (TTI Inc, etc.)
        # removed: "Milwaukee" — ville du Wisconsin, FP géographiques
        "Ryobi",
    ],
    "0016.HK":  ["Sun Hung Kai Properties"],
    "0011.HK":  ["Hang Seng Bank"],
    "9999.HK":  ["NetEase"],
    "6862.HK":  ["Haidilao"],
    "1810.HK":  ["Xiaomi"],
    "0001.HK":  ["CK Hutchison", "Hutchison"],
    "0066.HK":  ["MTR Corporation", "MTR"],
    "0002.HK":  ["CLP Holdings", "CLP"],
    "0175.HK":  ["Geely", "Geely Auto", "Volvo Cars"],
    "2382.HK":  ["Sunny Optical"],
    "6098.HK":  ["Country Garden Services"],
    "0267.HK":  ["CITIC"],
    "1093.HK":  ["CSPC Pharmaceutical"],
    "0288.HK":  ["WH Group", "Smithfield Foods"],
    "0762.HK":  ["China Unicom"],
    "0836.HK":  ["China Resources Power"],

    # ─── ASIA — Corée du Sud (.KS) ───────────────────────────────────────────
    "005930.KS": [
        "Samsung", "Samsung Electronics",
        # removed: "Galaxy" — galaxie astronomique, aussi utilisé par d'autres marques Android
    ],
    "000660.KS": ["SK Hynix", "Hynix"],
    "035420.KS": ["NAVER"],
    "051910.KS": ["LG Chem"],
    "006400.KS": ["Samsung SDI"],
    "005380.KS": ["Hyundai", "Hyundai Motor"],
    "000270.KS": ["Kia", "Kia Motors"],
    "068270.KS": ["Celltrion"],
    "105560.KS": ["KB Financial", "KB Kookmin"],
    "055550.KS": ["Shinhan Financial", "Shinhan"],
    "028260.KS": ["Samsung C&T"],
    "009150.KS": ["Samsung Electro-Mechanics"],
    "034730.KS": [
        # removed: "SK" — 2 lettres, très générique (Ski, Slovakia, etc.)
        "SK Group",
    ],
    "066570.KS": [
        "LG Electronics",
        # removed: "LG" — 2 lettres, ambigu hors contexte coréen
    ],
    "032830.KS": ["Samsung Life Insurance"],
    "017670.KS": ["SK Telecom"],
    "030200.KS": ["KT Corporation"],
    "003550.KS": ["LG Corp"],
    "018260.KS": ["Samsung SDS"],
    "011200.KS": ["HMM", "Hyundai Merchant Marine"],
    "004020.KS": ["Hyundai Steel"],

    # ─── ASIA — Inde (.NS) ───────────────────────────────────────────────────
    "RELIANCE.NS":   [
        # removed: "Reliance" — mot anglais courant (reliance on, self-reliance, etc.)
        "Reliance Industries", "Jio", "Mukesh Ambani", "Reliance Retail",
    ],
    "TCS.NS":        [
        "TCS", "Tata Consultancy Services",
        # removed: "Tata" — conglomérat multi-tickers (Tata Motors, Tata Steel, etc.), FP inter-tickers
    ],
    "HDFCBANK.NS":   ["HDFC Bank", "HDFC"],
    "BHARTIARTL.NS": ["Bharti Airtel", "Airtel"],
    "ICICIBANK.NS":  ["ICICI Bank", "ICICI"],
    "INFY.NS":       ["Infosys"],
    "SBIN.NS":       ["SBI", "State Bank of India"],
    "HINDUNILVR.NS": ["Hindustan Unilever", "HUL"],
    "ITC.NS":        [
        "ITC Limited",
        # removed: "ITC" — acronyme partagé (International Trade Centre, ITC Hotels, etc.)
    ],
    "KOTAKBANK.NS":  ["Kotak Mahindra Bank", "Kotak"],
    "LT.NS":         ["Larsen & Toubro", "L&T"],
    "AXISBANK.NS":   ["Axis Bank"],
    "BAJFINANCE.NS": ["Bajaj Finance"],
    "MARUTI.NS":     ["Maruti Suzuki", "Maruti"],
    "TITAN.NS":      [
        # removed: "Titan" — très générique (lune de Saturne, fusée Titan, titan de l'industrie)
        "Tanishq",
    ],
    "SUNPHARMA.NS":  ["Sun Pharma", "Sun Pharmaceutical"],
    "WIPRO.NS":      ["Wipro"],
    "HCLTECH.NS":    ["HCL Technologies", "HCL Tech", "HCL"],
    "ADANIENT.NS":   ["Adani Enterprises", "Adani", "Gautam Adani"],
    "ADANIPORTS.NS": ["Adani Ports"],
    "TATAMOTORS.NS": [
        "Tata Motors",
        # removed: "Jaguar" — animal emblématique, aussi brand automobile distinct de Tata depuis cession
        "Land Rover", "JLR",
    ],
    "TATASTEEL.NS":  ["Tata Steel"],
    "NESTLEIND.NS":  ["Nestlé India", "Maggi"],

    # ─── ASIA — Taiwan (.TW) ─────────────────────────────────────────────────
    "2330.TW":  ["TSMC", "Taiwan Semiconductor", "Taiwan Semi", "Morris Chang"],
    "2317.TW":  [
        "Foxconn", "Hon Hai",
        # removed: "Pegatron" — société distincte cotée séparément, pas une filiale Foxconn
    ],
    "2454.TW":  ["MediaTek"],
    "2412.TW":  ["Chunghwa Telecom"],
    "2308.TW":  ["Delta Electronics"],
    "2303.TW":  ["United Microelectronics", "UMC"],
    "3711.TW":  ["ASE Technology"],
    "2357.TW":  ["Asustek", "Asus"],
    "2002.TW":  ["China Steel"],
    "2327.TW":  ["Yageo"],
    "2382.TW":  ["Quanta Computer"],

    # ─── ASIA — Australie (.AX) ──────────────────────────────────────────────
    "BHP.AX":   ["BHP", "BHP Group"],
    "CBA.AX":   ["Commonwealth Bank", "CommBank"],
    "CSL.AX":   ["CSL Limited", "CSL Behring"],
    "NAB.AX":   ["National Australia Bank", "NAB"],
    "WBC.AX":   ["Westpac"],
    "ANZ.AX":   ["ANZ", "Australia and New Zealand Banking"],
    "WES.AX":   ["Wesfarmers", "Bunnings"],
    "WOW.AX":   ["Woolworths"],
    "MQG.AX":   ["Macquarie Group", "Macquarie"],
    "RIO.AX":   ["Rio Tinto"],
    "FMG.AX":   ["Fortescue", "Fortescue Metals"],
    "TLS.AX":   ["Telstra"],
    "GMG.AX":   ["Goodman Group"],

    # ─── ASIA — Singapour (.SI) ───────────────────────────────────────────────
    "D05.SI":   ["DBS", "DBS Bank"],
    "O39.SI":   ["OCBC", "OCBC Bank"],
    "U11.SI":   ["UOB", "United Overseas Bank"],
    "Z74.SI":   ["Singtel", "Singapore Telecommunications"],
    "C6L.SI":   [
        "Singapore Airlines",
        # removed: "SIA" — acronyme ambigu (Securities Industry Assoc., Spain, etc.)
    ],
    "G13.SI":   ["Genting Singapore"],
    "C09.SI":   ["City Developments"],
    "BS6.SI":   ["Yangzijiang Shipbuilding"],
    "S58.SI":   ["SATS"],
    "BN4.SI":   ["Keppel Corporation", "Keppel"],

    # ─── US — ADRs chinois ────────────────────────────────────────────────────
    "BABA":  ["Alibaba", "Taobao", "Tmall", "AliExpress", "Jack Ma"],
    "JD":    ["JD.com", "Jingdong"],
    "PDD":   ["Pinduoduo", "Temu", "PDD Holdings"],
    "BIDU":  ["Baidu", "Ernie Bot"],
    "NIO":   ["NIO", "Nio Electric"],
    "XPEV":  ["XPeng", "XPEV"],
    "LI":    ["Li Auto", "Li Xiang"],
    "TCOM":  ["Trip.com", "Ctrip"],
    "EDU":   ["New Oriental", "New Oriental Education"],
    "NTES":  ["NetEase"],
    "IQ":    ["iQIYI"],
    "BILI":  ["Bilibili"],
    "ZTO":   ["ZTO Express"],
    "YUMC":  ["Yum China", "KFC China", "Pizza Hut China"],
    "WB":    ["Weibo"],
}
