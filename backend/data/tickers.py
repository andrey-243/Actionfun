"""
Top tickers par région — source de vérité pour le batch scraper.
~250 US · ~250 EU · ~250 Asie/Pacifique
"""

US = [
    # Mega cap tech
    "AAPL","MSFT","NVDA","AMZN","GOOGL","META","TSLA","AVGO","ORCL","AMD",
    "QCOM","TXN","AMAT","LRCX","KLAC","MCHP","SNPS","CDNS","ADI","MU",
    "INTC","ARM","PANW","CRWD","FTNT","ZS","NET","OKTA","DDOG","SNOW",
    "NOW","CRM","INTU","ADBE","WDAY","TEAM","MDB","HUBS","SPLK","VEEV",
    # Finance
    "JPM","BAC","WFC","GS","MS","C","USB","PNC","TFC","COF",
    "AXP","V","MA","PYPL","SQ","COIN","SCHW","BLK","BX","KKR",
    "APO","ARES","ICE","CME","NDAQ","SPGI","MCO","MSCI","FDS","BR",
    # Santé
    "LLY","UNH","JNJ","ABBV","MRK","PFE","BMY","AMGN","GILD","REGN",
    "VRTX","BIIB","ISRG","MDT","ABT","DHR","TMO","A","IQV","CRL",
    "ZTS","EW","SYK","BSX","BDX","BAX","HOLX","IDXX","TECH","PODD",
    # Conso
    "WMT","COST","TGT","AMZN","HD","LOW","TJX","ROST","DLTR","DG",
    "MCD","SBUX","CMG","YUM","DPZ","QSR","DNUT","SHAK","TXRH","CAKE",
    "PG","KO","PEP","PM","MO","MDLZ","GIS","K","CPB","SJM",
    "CL","CHD","EL","COTY","REV","SPB","HRL","TSN","CAG","LW",
    # Energie
    "XOM","CVX","COP","EOG","PXD","DVN","HAL","SLB","BKR","HES",
    "OXY","MPC","PSX","VLO","FANG","CTRA","APA","AR","EQT","RRC",
    # Industriels
    "GE","BA","RTX","LMT","NOC","GD","HII","TDG","HWM","SPR",
    "CAT","DE","HON","ETN","EMR","ROK","PH","ITW","CARR","OTIS",
    "UPS","FDX","CHRW","EXPD","JBHT","XPO","ODFL","SAIA","NSC","UNP",
    # Immobilier/Utilities
    "NEE","DUK","SO","AEP","EXC","SRE","D","PCG","XEL","ES",
    "AMT","PLD","EQIX","DLR","SPG","O","WY","PSA","AVB","EQR",
    # Divers
    "BKNG","ABNB","UBER","LYFT","DASH","EXPE","TRIP","MKTS","MGM","LVS",
    "NFLX","DIS","CMCSA","PARA","WBD","FOXA","NYT","IAC","MTCH","PINS",
    "SPOT","RBLX","U","DKNG","PENN","CHTR","TMUS","VZ","T","LUMN",
    "NEM","FCX","AA","CLF","NUE","STLD","X","RS","CMC","MP",
    "LIN","APD","DD","DOW","PPG","SHW","ECL","IFF","RPM","FMC",
]

EU = [
    # France — Euronext Paris (.PA)
    "MC.PA","TTE.PA","SAN.PA","AIR.PA","OR.PA","BNP.PA","DG.PA","SU.PA",
    "AI.PA","RI.PA","CAP.PA","DSY.PA","VIE.PA","KER.PA","GLE.PA","AXA.PA",
    "ORA.PA","SAF.PA","HO.PA","LR.PA","CS.PA","EN.PA","ML.PA","SGO.PA",
    "SG.PA","CA.PA","UG.PA","AC.PA","RNO.PA","PUB.PA","TEP.PA","EDEN.PA",
    "FP.PA","ENGI.PA","DBV.PA","STMPA.PA","VK.PA","ERF.PA","BON.PA","TCFP.PA",
    # Allemagne — Xetra (.DE)
    "SAP.DE","SIE.DE","ALV.DE","DTE.DE","BAYN.DE","BMW.DE","MBG.DE","BAS.DE",
    "MUV2.DE","VOW3.DE","IFX.DE","ADS.DE","RWE.DE","DBK.DE","CON.DE","EOAN.DE",
    "FME.DE","FRE.DE","MTX.DE","HEI.DE","MRK.DE","HEN3.DE","VNA.DE","DHL.DE",
    "ENR.DE","BOSS.DE","WDI.DE","SHL.DE","AIR.DE","1COV.DE","DHER.DE","PSM.DE",
    "SY1.DE","NDX1.DE","BCMB.DE","EVD.DE","DWS.DE","GFJ.DE","KNEBV.DE","RAA.DE",
    # Royaume-Uni — LSE (.L)
    "SHEL.L","AZN.L","HSBA.L","ULVR.L","BP.L","GSK.L","RIO.L","DGE.L",
    "BATS.L","REL.L","BHP.L","PRU.L","LSEG.L","AAL.L","GLEN.L","NXT.L",
    "III.L","SSE.L","BARC.L","LLOY.L","RB.L","IMB.L","AHT.L","CRH.L",
    "ANTO.L","EXPN.L","FLTR.L","MXCT.L","SGRO.L","KGF.L","WTB.L","AUTO.L",
    "OCDO.L","MNG.L","NWG.L","STAN.L","VOD.L","BT-A.L","SKG.L","MNDI.L",
    # Pays-Bas — Euronext Amsterdam (.AS)
    "ASML.AS","HEIA.AS","RAND.AS","NN.AS","PHIA.AS","URW.AS","ABN.AS","INGA.AS",
    "MT.AS","AEX.AS","DSM.AS","AKZA.AS","WKL.AS","AGN.AS","TKWY.AS","BESI.AS",
    # Suisse — SIX (.SW)
    "NESN.SW","ROG.SW","NOVN.SW","CFR.SW","UBSG.SW","ZURN.SW","CSGN.SW","SREN.SW",
    "ABBN.SW","GIVN.SW","GEBN.SW","BALN.SW","LONN.SW","SIKA.SW","SLHN.SW","SCMN.SW",
    # Italie — Borsa Italiana (.MI)
    "ENI.MI","ENEL.MI","ISP.MI","UCG.MI","TIT.MI","LUX.MI","MB.MI","ATL.MI",
    "PRY.MI","PST.MI","AMP.MI","BAMI.MI","SRG.MI","FCA.MI","IF.MI","RACE.MI",
    # Espagne — BME (.MC)
    "SAN.MC","BBVA.MC","IBE.MC","TEF.MC","ITX.MC","REP.MC","AMS.MC","CLNX.MC",
    "ENG.MC","FER.MC","ACS.MC","MEL.MC","GRF.MC","ACX.MC","COL.MC","VIS.MC",
    # Suède — Nasdaq Stockholm (.ST)
    "VOLV-B.ST","ERIC-B.ST","SEB-A.ST","SHB-A.ST","SWED-A.ST","ESSITY-B.ST",
    "ATCO-A.ST","INVE-B.ST","SAND.ST","SKF-B.ST","ALFA.ST","HEXA-B.ST",
    # Danemark — Nasdaq Copenhagen (.CO)
    "NOVO-B.CO","MAERSK-B.CO","DSV.CO","CARL-B.CO","ORSTED.CO","GN.CO","COLO-B.CO",
    # Finlande — Nasdaq Helsinki (.HE)
    "NOKIA.HE","FORTUM.HE","NESTE.HE","KNEBV.HE","STERV.HE","UPM.HE","WRT1V.HE",
    # Belgique (.BR)
    "AB-INBEV.BR","UCB.BR","KBC.BR","SOLB.BR","GBL.BR","ACKB.BR","ABI.BR",
    # Portugal (.LS)
    "EDP.LS","GALP.LS","BCP.LS","EGL.LS","SEM.LS",
    # Irlande (.IR)
    "CRH.IR","AIRP.IR","BHVN.IR","KRZ.IR","AIB.IR",
]

ASIA = [
    # Japon — Tokyo Stock Exchange (.T)
    "7203.T","6758.T","9432.T","6861.T","8306.T","9433.T","6902.T","4063.T",
    "7974.T","9984.T","8316.T","4502.T","6954.T","7267.T","6501.T","5108.T",
    "4661.T","8035.T","9022.T","3382.T","2914.T","6098.T","7270.T","6367.T",
    "8766.T","4568.T","9020.T","6971.T","9613.T","9434.T","8411.T","7733.T",
    "6594.T","6723.T","4519.T","6702.T","9107.T","5401.T","3659.T","7832.T",
    "6752.T","4543.T","8058.T","8001.T","7741.T","4151.T","7011.T","8802.T",
    "6762.T","9735.T",
    # Chine / Hong Kong — HKEX (.HK)
    "0700.HK","0939.HK","1299.HK","3988.HK","0941.HK","2318.HK","1398.HK",
    "0005.HK","9988.HK","0388.HK","2628.HK","0883.HK","1211.HK","9618.HK",
    "0960.HK","3690.HK","1024.HK","0669.HK","0016.HK","0011.HK","2269.HK",
    "9999.HK","6862.HK","1810.HK","0001.HK","0066.HK","0002.HK","0027.HK",
    "0003.HK","1177.HK","0762.HK","0175.HK","2382.HK","6098.HK","0267.HK",
    "1093.HK","0288.HK","0823.HK","0012.HK","0836.HK",
    # Corée du Sud — KRX (.KS)
    "005930.KS","000660.KS","035420.KS","051910.KS","006400.KS","005380.KS",
    "000270.KS","068270.KS","105560.KS","055550.KS","028260.KS","009150.KS",
    "034730.KS","066570.KS","096770.KS","032830.KS","017670.KS","030200.KS",
    "003550.KS","018260.KS","011200.KS","010130.KS","009830.KS","012330.KS",
    "086790.KS","010950.KS","047050.KS","032640.KS","011170.KS","004020.KS",
    # Inde — NSE (.NS)
    "RELIANCE.NS","TCS.NS","HDFCBANK.NS","BHARTIARTL.NS","ICICIBANK.NS",
    "INFY.NS","SBIN.NS","HINDUNILVR.NS","ITC.NS","KOTAKBANK.NS","LT.NS",
    "AXISBANK.NS","BAJFINANCE.NS","MARUTI.NS","TITAN.NS","SUNPHARMA.NS",
    "WIPRO.NS","ULTRACEMCO.NS","ASIANPAINT.NS","HCLTECH.NS","POWERGRID.NS",
    "NTPC.NS","ONGC.NS","BAJAJFINSV.NS","ADANIENT.NS","ADANIPORTS.NS",
    "TATAMOTORS.NS","TATASTEEL.NS","JSWSTEEL.NS","GRASIM.NS","NESTLEIND.NS",
    # Taiwan — TWSE (.TW)
    "2330.TW","2317.TW","2454.TW","2412.TW","2308.TW","2882.TW","2881.TW",
    "2886.TW","2891.TW","2303.TW","2379.TW","2395.TW","3711.TW","2357.TW",
    "2002.TW","1303.TW","1301.TW","2207.TW","2327.TW","2382.TW",
    # Australie — ASX (.AX)
    "BHP.AX","CBA.AX","CSL.AX","NAB.AX","WBC.AX","ANZ.AX","WES.AX","WOW.AX",
    "MQG.AX","RIO.AX","FMG.AX","NCM.AX","TLS.AX","WPL.AX","STO.AX","AMC.AX",
    "TCL.AX","GMG.AX","ALL.AX","QBE.AX","SHL.AX","BXB.AX","CPU.AX","REA.AX",
    # Singapour — SGX (.SI)
    "D05.SI","O39.SI","U11.SI","Z74.SI","C6L.SI","G13.SI","C09.SI","Y92.SI",
    "BS6.SI","S58.SI","9CI.SI","C52.SI","BN4.SI","V03.SI","F34.SI",
    # Stocks chinois cotés aux US (ADRs)
    "BABA","JD","PDD","BIDU","NIO","XPEV","LI","TCOM","EDU","TAL",
    "VIPS","NTES","IQ","BILI","YMM","ZTO","YUMC","CTRP","WB","MOMO",
]

ALL_TICKERS = US + EU + ASIA

def get_by_region(region: str) -> list[str]:
    return {"US": US, "EU": EU, "ASIA": ASIA}.get(region.upper(), [])
