"""Annuaire des sociétés cotées : nom, secteur, pays et ISIN d'un ticker Yahoo.

Source : FinanceDatabase (github.com/JerBouma/FinanceDatabase, licence MIT),
plus de 120 000 actions, base statique sans clé ni cours. Chargée une fois
par processus (environ une seconde) ; si elle est indisponible, le bot
continue avec les seuls tickers.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass

import pandas as pd

log = logging.getLogger(__name__)

# Places cotant en euros ou en dollars : le bot ne convertit que ces deux devises.
PLACES = {"PAR", "AMS", "BRU", "LIS", "GER", "MIL", "MCE", "HEL", "VIE", "ISE",
          "NMS", "NYQ", "NGM", "NCM", "ASE"}
TAILLES = {"Mega Cap": 0, "Large Cap": 1, "Mid Cap": 2, "Small Cap": 3, "Micro Cap": 4, "Nano Cap": 5}

PAYS = {"france": "France", "allemagne": "Germany", "pays-bas": "Netherlands", "belgique": "Belgium",
        "italie": "Italy", "espagne": "Spain", "portugal": "Portugal", "finlande": "Finland",
        "autriche": "Austria", "irlande": "Ireland", "usa": "United States", "us": "United States",
        "etats-unis": "United States", "europe": "*europe"}
# place principale de chaque pays : une seule cotation par société, celle-ci de préférence
PLACE_DU_PAYS = {"France": "PAR", "Germany": "GER", "Netherlands": "AMS", "Belgium": "BRU", "Italy": "MIL",
                 "Spain": "MCE", "Portugal": "LIS", "Finland": "HEL", "Austria": "VIE", "Ireland": "ISE"}
EUROPE = {"France", "Germany", "Netherlands", "Belgium", "Italy", "Spain", "Portugal", "Finland",
          "Austria", "Ireland", "Luxembourg"}

# mots français → termes de secteur ou d'industrie de la base (en anglais)
SECTEURS = {"defense": "Aerospace & Defense", "aeronautique": "Aerospace & Defense",
            "sante": "Health Care", "pharmacie": "Pharmaceuticals", "biotech": "Biotechnology",
            "banque": "Banks", "banques": "Banks", "assurance": "Insurance", "energie": "Energy",
            "petrole": "Oil, Gas", "electricite": "Electric Utilities", "luxe": "Luxury",
            "semi-conducteurs": "Semiconductors", "puces": "Semiconductors", "logiciel": "Software",
            "automobile": "Automobiles", "chimie": "Chemicals", "mines": "Metals & Mining",
            "telecoms": "Telecommunication", "immobilier": "Real Estate", "construction": "Construction",
            "electrique": "Electrical Equipment", "transport": "Transportation", "agroalimentaire": "Food"}

FORMES = re.compile(r"\b(s\.?a\.?|s\.e\.?|se|sa|s\.a|plc|ag|n\.?v\.?|a/s|asa|ab|oyj|spa|s\.p\.a\.?|inc\.?|corp\.?|"
                    r"corporation|company|co\.?|ltd\.?|limited|group|holding|holdings|societe europeenne|"
                    r"societe anonyme|and)\b", re.I)


@dataclass
class Societe:
    ticker: str
    nom: str
    secteur: str = ""
    industrie: str = ""
    pays: str = ""
    isin: str = ""
    taille: str = ""


_base: pd.DataFrame | None = None


def base() -> pd.DataFrame | None:
    global _base
    if _base is None:
        try:
            import financedatabase as fd

            _base = fd.Equities().data
        except Exception as exc:  # module absent, GitHub injoignable…
            log.warning("Annuaire FinanceDatabase indisponible : %s", type(exc).__name__)
            _base = pd.DataFrame()
    return _base if not _base.empty else None


def _valeur(ligne, champ: str) -> str:
    v = ligne.get(champ)
    return "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v)


def _societe(ticker: str, ligne) -> Societe:
    return Societe(ticker, _valeur(ligne, "name") or ticker, _valeur(ligne, "sector"), _valeur(ligne, "industry"),
                   _valeur(ligne, "country"), _valeur(ligne, "isin"), _valeur(ligne, "market_cap"))


def societe(ticker: str) -> Societe | None:
    df = base()
    if df is None or ticker not in df.index:
        return None
    ligne = df.loc[ticker]
    if isinstance(ligne, pd.DataFrame):
        ligne = ligne.iloc[0]
    return _societe(ticker, ligne)


def nom(ticker: str) -> str:
    """Nom court de la société, sinon le ticker."""
    s = societe(ticker)
    return court(s.nom) if s else ticker


def court(nom_long: str) -> str:
    """« LVMH Moët Hennessy - Louis Vuitton, Societe Europeenne » → « LVMH Moët Hennessy »."""
    nom_long = re.split(r",| - ", nom_long)[0]
    reste = FORMES.sub("", nom_long).strip(" .,")
    return re.sub(r"\s{2,}", " ", reste) or nom_long


def cle(nom_long: str) -> str:
    """Clé de rapprochement entre sources : minuscules, sans accents ni forme juridique.
    « Eli Lilly and Company » → « eli lilly » ; « SANOFI AVENTIS US » contient « sanofi »."""
    texte = unicodedata.normalize("NFKD", court(nom_long)).encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", texte).split())


def _sans_accents(texte: str) -> str:
    return unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode().lower()


def chercher(texte: str, limite: int = 10) -> list[Societe]:
    """Recherche libre : mots du nom, d'un secteur (en français ou en anglais) ou
    d'un pays. « defense france », « luxe », « nvidia ». Les plus grandes d'abord,
    une seule cotation par société, en euros ou en dollars seulement."""
    df = base()
    if df is None:
        return []
    choix = df[df["exchange"].isin(PLACES) & ~df["delisted"].fillna(False).astype(bool)]
    for mot in _sans_accents(texte).split():
        pays = PAYS.get(mot)
        if pays == "*europe":
            choix = choix[choix["country"].isin(EUROPE)]
            continue
        if pays:
            choix = choix[choix["country"] == pays]
            continue
        terme = SECTEURS.get(mot, mot)
        motif = re.escape(terme)
        masque = pd.Series(False, index=choix.index)
        for champ in ("name", "sector", "industry_group", "industry"):
            masque |= choix[champ].fillna("").str.contains(motif, case=False, regex=True)
        choix = choix[masque]
    if choix.empty:
        return []
    chez_soi = choix["exchange"] == choix["country"].map(PLACE_DU_PAYS).fillna("")
    chez_soi |= (choix["country"] == "United States") & choix["exchange"].isin({"NMS", "NYQ", "NGM", "NCM", "ASE"})
    choix = choix.assign(_taille=choix["market_cap"].map(TAILLES).fillna(9), _ailleurs=~chez_soi,
                         _cle=choix["isin"].fillna(choix["name"]))
    # une seule cotation par société, sur sa place d'origine si elle y est cotée
    choix = choix.sort_values(["_ailleurs", "_taille"]).drop_duplicates("_cle").sort_values(["_taille", "_ailleurs"])
    return [_societe(t, ligne) for t, ligne in choix.head(limite).iterrows()]
