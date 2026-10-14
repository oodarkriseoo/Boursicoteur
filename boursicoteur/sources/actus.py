"""Lecture des flux RSS d'actualités."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import quote

log = logging.getLogger(__name__)

URL_YAHOO = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"


@dataclass(frozen=True)
class Actu:
    titre: str
    lien: str
    source: str
    publie: str = ""
    resume: str = ""
    ticker: str | None = None


def flux_par_valeur(tickers: list[str]) -> dict[str, str]:
    return {t: URL_YAHOO.format(ticker=quote(t)) for t in tickers}


def lire_flux(url: str, ticker: str | None = None, limite: int = 20,
              cache: dict[str, dict[str, str]] | None = None) -> list[Actu]:
    """``cache`` garde l'ETag et la date de chaque flux : relu souvent, un flux
    inchangé ne renvoie rien et ne coûte presque rien au serveur."""
    import feedparser

    deja = (cache or {}).get(url, {})
    try:
        flux = feedparser.parse(url, agent="Boursicoteur/0.1", etag=deja.get("etag"),
                                modified=deja.get("modified"))
    except Exception as exc:
        log.warning("Flux %s illisible : %s", url, exc)
        return []
    if cache is not None and (flux.get("etag") or flux.get("modified")):
        cache[url] = {"etag": flux.get("etag"), "modified": flux.get("modified")}
    source = flux.feed.get("title", url) if getattr(flux, "feed", None) else url
    actus = []
    for entree in flux.entries[:limite]:
        lien = entree.get("link") or entree.get("id")
        titre = (entree.get("title") or "").strip()
        if not lien or not titre:
            continue
        actus.append(
            Actu(
                titre=titre,
                lien=lien,
                source=source,
                publie=entree.get("published", "") or entree.get("updated", ""),
                resume=_nettoyer(entree.get("summary", ""))[:500],
                ticker=ticker,
            )
        )
    return actus


def collecter(flux_generaux: list[str], tickers: list[str]) -> list[Actu]:
    actus: list[Actu] = []
    for url in flux_generaux:
        actus += lire_flux(url)
    for ticker, url in flux_par_valeur(tickers).items():
        actus += lire_flux(url, ticker=ticker, limite=10)
    # dédoublonnage par lien, en gardant le premier
    vus: dict[str, Actu] = {}
    for actu in actus:
        vus.setdefault(actu.lien, actu)
    return list(vus.values())


def mentionne(actu: Actu, mots_cles: list[str]) -> bool:
    texte = f" {actu.titre} {actu.resume} ".lower()
    return any(mot.lower() in texte for mot in mots_cles)


def _nettoyer(html: str) -> str:
    import re

    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()
