"""Introductions en bourse à venir (calendrier Nasdaq) et nouveaux dossiers
d'introduction déposés auprès de la SEC (formulaires S-1 et F-1).

Le calendrier Nasdaq est une API publique mais non documentée : son format
peut changer. Les erreurs sont journalisées et l'outil continue sans.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

import requests

log = logging.getLogger(__name__)

URL_NASDAQ = "https://api.nasdaq.com/api/ipo/calendar?date={mois}"
URL_SEC = (
    "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type={formulaire}"
    "&company=&dateb=&owner=include&count=40&output=atom"
)


@dataclass(frozen=True)
class Introduction:
    societe: str
    statut: str  # « à venir », « cotée », « dossier déposé »
    ticker: str = ""
    marche: str = ""
    prix: str = ""
    date: str = ""
    montant: str = ""
    lien: str = ""


def calendrier_nasdaq(mois: date | None = None) -> list[Introduction]:
    mois = mois or date.today()
    try:
        reponse = requests.get(
            URL_NASDAQ.format(mois=mois.strftime("%Y-%m")),
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
            timeout=20,
        )
        reponse.raise_for_status()
        return lire_nasdaq(reponse.json())
    except Exception as exc:
        log.warning("Calendrier Nasdaq indisponible : %s", exc)
        return []


def lire_nasdaq(donnees: dict) -> list[Introduction]:
    data = (donnees or {}).get("data") or {}
    sections = {
        "à venir": (data.get("upcoming") or {}).get("upcomingTable") or {},
        "cotée": data.get("priced") or {},
    }
    resultat = []
    for statut, table in sections.items():
        for ligne in table.get("rows") or []:
            resultat.append(
                Introduction(
                    societe=ligne.get("companyName", "").strip(),
                    statut=statut,
                    ticker=ligne.get("proposedTickerSymbol", "") or "",
                    marche=ligne.get("proposedExchange", "") or "",
                    prix=ligne.get("proposedSharePrice", "") or "",
                    date=ligne.get("expectedPriceDate") or ligne.get("pricedDate") or "",
                    montant=ligne.get("dollarValueOfSharesOffered", "") or "",
                )
            )
    return [i for i in resultat if i.societe]


def depots_sec(user_agent: str) -> list[Introduction]:
    import feedparser

    resultat = []
    for formulaire in ("S-1", "F-1"):
        try:
            reponse = requests.get(
                URL_SEC.format(formulaire=formulaire),
                headers={"User-Agent": user_agent},
                timeout=20,
            )
            reponse.raise_for_status()
        except Exception as exc:
            log.warning("Flux SEC %s indisponible : %s", formulaire, exc)
            continue
        resultat += lire_sec(feedparser.parse(reponse.text).entries, formulaire)
    return resultat


def lire_sec(entrees: list, formulaire: str) -> list[Introduction]:
    resultat = []
    for entree in entrees:
        titre = entree.get("title", "")
        # Exemple de titre : « S-1 - Acme AI Inc (0001234567) (Filer) »
        if not titre.startswith(f"{formulaire} "):
            continue  # ignore les amendements (S-1/A) et autres formulaires
        societe = titre.split(" - ", 1)[-1].split(" (")[0].strip()
        resultat.append(
            Introduction(
                societe=societe,
                statut="dossier déposé",
                date=(entree.get("updated") or "")[:10],
                lien=entree.get("link", ""),
            )
        )
    return resultat
