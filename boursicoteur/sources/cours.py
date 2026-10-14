"""Historique des cours via Yahoo Finance (yfinance).

yfinance est gratuit mais non officiel : si Yahoo change son site, il peut
cesser de fonctionner quelque temps. Le reste de l'outil continue alors sans
les valeurs manquantes.
"""

from __future__ import annotations

import logging

import pandas as pd

log = logging.getLogger(__name__)


def historiques(tickers: list[str], periode: str = "6mo") -> dict[str, pd.DataFrame]:
    """Renvoie {ticker: DataFrame(Open, High, Low, Close, Volume)}. Les
    tickers en échec sont absents du résultat."""
    import yfinance as yf

    if not tickers:
        return {}
    try:
        brut = yf.download(
            tickers,
            period=periode,
            group_by="ticker",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
    except Exception as exc:  # réseau, format, etc.
        log.warning("Téléchargement des cours impossible : %s", exc)
        return {}
    return decouper(brut, tickers)


def decouper(brut: pd.DataFrame, tickers: list[str]) -> dict[str, pd.DataFrame]:
    resultat: dict[str, pd.DataFrame] = {}
    if brut is None or brut.empty:
        return resultat
    multi = isinstance(brut.columns, pd.MultiIndex)
    for ticker in tickers:
        if multi:
            if ticker not in brut.columns.get_level_values(0):
                continue
            df = brut[ticker]
        else:
            df = brut
        df = df.dropna(how="all")
        if not df.empty and "Close" in df:
            resultat[ticker] = df
    return resultat
