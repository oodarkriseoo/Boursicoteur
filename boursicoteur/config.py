"""Chargement de la configuration (config.yaml) avec des valeurs par défaut."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

DEFAUTS: dict[str, Any] = {
    "portefeuille": [],
    "reference": "CW8.PA",
    "seuils": {
        "baisse_seance_pct": 5.0,
        "chute_rapide_pct": 3.0,
        "risque_alerte": 50,
        "baisse_depuis_sommet_pct": 15.0,
        "perte_vs_prix_revient_pct": 20.0,
        "vix_alerte": 30.0,
        "volume_inhabituel_x": 3.0,
    },
    "theme_ia": {"univers": [], "mots_cles": [], "pistes_max": 5},
    "opportunites": {"activee": True, "seuil_alerte": 60, "alertes_max": 3,
                     "modele": "claude-sonnet-5-5"},
    "conseils": {
        "actives": True,
        "poche_eur": 1000.0,
        "risque_par_position_pct": 2.0,
        "position_max_pct": 25.0,
        "frais_ordre_eur": 1.0,
        "ratio_objectif": 2.0,
    },
    "introductions": {
        "nasdaq": True,
        "sec_s1": True,
        "sec_user_agent": "Boursicoteur contact@example.com",
    },
    "flux_rss": [],
    "ia": {
        "activee": True,
        "modele": "claude-opus-5-5",
        "effort": "low",
        "cle_env": "ANTHROPIC_API_KEY",
    },
    "notifications": {
        "telegram": {"token_env": "TELEGRAM_BOT_TOKEN", "chat_id_env": "TELEGRAM_CHAT_ID"},
        "ntfy": {"topic_env": "NTFY_TOPIC"},
    },
    "planification": {"intervalle_veille_min": 30, "heure_resume": "08:00", "jour_bilan": 1},
    "base_de_donnees": "data/journal.sqlite",
}


def _fusionner(base: dict[str, Any], surcharge: dict[str, Any]) -> dict[str, Any]:
    resultat = copy.deepcopy(base)
    for cle, valeur in surcharge.items():
        if isinstance(valeur, dict) and isinstance(resultat.get(cle), dict):
            resultat[cle] = _fusionner(resultat[cle], valeur)
        else:
            resultat[cle] = valeur
    return resultat


def charger(chemin: str | Path = "config.yaml") -> dict[str, Any]:
    chemin = Path(chemin)
    if not chemin.exists():
        raise FileNotFoundError(
            f"{chemin} introuvable. Copie config.example.yaml en config.yaml et adapte-le."
        )
    contenu = yaml.safe_load(chemin.read_text(encoding="utf-8")) or {}
    return _fusionner(DEFAUTS, contenu)


def tickers_suivis(config: dict[str, Any]) -> list[str]:
    """Portefeuille + univers IA + référence, sans doublon, dans l'ordre."""
    tickers = [p["ticker"] for p in config["portefeuille"]]
    tickers += config["theme_ia"]["univers"]
    tickers.append(config["reference"])
    return list(dict.fromkeys(tickers))
