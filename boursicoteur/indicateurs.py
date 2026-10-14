"""Indicateurs techniques simples, calculés sur un historique de cours.

Chaque fonction prend un DataFrame avec au moins les colonnes ``Close`` et,
selon le cas, ``Volume``, trié du plus ancien au plus récent.
"""

from __future__ import annotations

import math

import pandas as pd


def variation_seance_pct(hist: pd.DataFrame) -> float | None:
    cloture = hist["Close"].dropna()
    if len(cloture) < 2:
        return None
    return (cloture.iloc[-1] / cloture.iloc[-2] - 1) * 100


def variation_sur_pct(hist: pd.DataFrame, jours: int) -> float | None:
    cloture = hist["Close"].dropna()
    if len(cloture) <= jours:
        return None
    return (cloture.iloc[-1] / cloture.iloc[-1 - jours] - 1) * 100


def ecart_au_plus_haut_pct(hist: pd.DataFrame) -> float | None:
    """Écart (négatif ou nul) entre le dernier cours et le plus haut de la période."""
    cloture = hist["Close"].dropna()
    if cloture.empty:
        return None
    return (cloture.iloc[-1] / cloture.max() - 1) * 100


def moyenne_mobile(hist: pd.DataFrame, jours: int) -> float | None:
    cloture = hist["Close"].dropna()
    if len(cloture) < jours:
        return None
    return float(cloture.iloc[-jours:].mean())


def rsi(hist: pd.DataFrame, jours: int = 14) -> float | None:
    """RSI de Wilder. Au-dessus de 70 : surachat ; en dessous de 30 : survente."""
    cloture = hist["Close"].dropna()
    if len(cloture) <= jours:
        return None
    delta = cloture.diff().dropna()
    hausses = delta.clip(lower=0).ewm(alpha=1 / jours, adjust=False).mean().iloc[-1]
    baisses = (-delta.clip(upper=0)).ewm(alpha=1 / jours, adjust=False).mean().iloc[-1]
    if baisses == 0:
        return 100.0
    return 100 - 100 / (1 + hausses / baisses)


def ratio_volume(hist: pd.DataFrame, jours: int = 20) -> float | None:
    """Volume du dernier jour rapporté à la moyenne des ``jours`` précédents."""
    if "Volume" not in hist:
        return None
    volume = hist["Volume"].dropna()
    if len(volume) <= jours:
        return None
    moyenne = volume.iloc[-1 - jours : -1].mean()
    if not moyenne or math.isnan(moyenne):
        return None
    return float(volume.iloc[-1] / moyenne)


def tendance(hist: pd.DataFrame) -> str:
    """« haussière », « baissière » ou « neutre » selon la position du cours
    par rapport aux moyennes mobiles 20 et 50 jours."""
    cloture = hist["Close"].dropna()
    mm20, mm50 = moyenne_mobile(hist, 20), moyenne_mobile(hist, 50)
    if cloture.empty or mm20 is None or mm50 is None:
        return "neutre"
    dernier = cloture.iloc[-1]
    if dernier > mm20 > mm50:
        return "haussière"
    if dernier < mm20 < mm50:
        return "baissière"
    return "neutre"
