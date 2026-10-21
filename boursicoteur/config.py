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
        "concentration_max_pct": 25.0,
        "drawdown_alerte_pct": 10.0,
    },
    "secteurs": {},  # ticker → secteur, en complément de memoire.SECTEURS
    "theme_ia": {"univers": [], "mots_cles": [], "pistes_max": 5},
    "themes": {},  # autres secteurs suivis, même forme que theme_ia (plus « libelle »)
    "opportunites": {"activee": True, "seuil_alerte": 60, "alertes_max": 3,
                     "modele": "claude-sonnet-5-5"},
    "evenements": {
        "actives": True,
        "intervalle_min": 5,        # relecture des flux
        "pause_ia_min": 15,         # au plus un appel à Claude par quart d'heure
        "appels_max_jour": 40,
        "confiance_alerte": 3,
        "alertes_max": 3,
        "modele": "claude-sonnet-5-5",
        "flux": [
            "https://feeds.bbci.co.uk/news/world/rss.xml",
            "https://feeds.bbci.co.uk/news/business/rss.xml",
            "https://www.lemonde.fr/international/rss_full.xml",
            "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/press-releases/rss.xml",
            "https://www.ema.europa.eu/en/news.xml",
            "https://www.war.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=400&Site=945&max=10",
            "https://www.eia.gov/rss/todayinenergy.xml",
        ],
    },
    "gdelt": {
        "actives": True,
        "fenetre_h": 4,             # couverture des 4 dernières heures…
        "facteur": 3,               # …au moins 3 fois sa moyenne des jours précédents
        "minimum": 30,              # et au moins 30 articles : un petit sujet ne compte pas
        "par_passage": 2,           # sujets lus à chaque relecture des flux (GDELT : 1 requête / 5 s)
        "repos_h": 24,              # un sujet signalé ne l'est plus pendant 24 h
        "positions": True,          # suit aussi le nom de chaque position
        "sujets": [
            '("Strait of Hormuz" OR "Red Sea shipping")',
            "tariffs",
            '"rare earths"',
            "OPEC",
            '("defense spending" OR "defence budget")',
            '"Taiwan Strait"',
            '"export controls" semiconductors',
            '("Ukraine ceasefire" OR "Ukraine peace talks")',
            '("GLP-1" OR semaglutide)',
            '("ECB rate" OR "Fed rate")',
        ],
    },
    "officiels": {"actives": True, "amf": True, "ema": True, "fda": True, "essais": True, "alertes_max": 5},
    "conseils": {
        "actives": True,
        "poche_eur": 1000.0,
        "risque_par_position_pct": 2.0,
        "position_max_pct": 25.0,
        "frais_ordre_eur": 1.0,
        "ratio_objectif": 2.0,
    },
    "courtier": {
        "type": "aucun",
        "hote": "127.0.0.1",
        "port": 4002,
        "client_id": 17,
        "compte_demo": True,
        "plafond_ordre_eur": 300.0,
        "plafond_jour_eur": 600.0,
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
        "espace_env": "ANTHROPIC_WORKSPACE_ID",
    },
    "notifications": {
        "telegram": {"token_env": "TELEGRAM_BOT_TOKEN", "chat_id_env": "TELEGRAM_CHAT_ID"},
        "ntfy": {"topic_env": "NTFY_TOPIC"},
    },
    "utilisateurs": {"max": 10},  # personnes acceptées sur le bot, administrateur compris
    "planification": {"intervalle_veille_min": 30, "heure_resume": "08:00", "resume_quotidien": False, "jour_bilan": 1},
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


def themes(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Tous les thèmes suivis, l'IA en premier, chacun complété par défaut :
    libellé, univers, mots-clés et nombre de pistes montrées dans le résumé."""
    tous = {"ia": {"libelle": "IA", **config["theme_ia"]}}
    for nom, theme in (config.get("themes") or {}).items():
        tous[nom] = {"libelle": nom.capitalize(), "univers": [], "mots_cles": [], "pistes_max": 3,
                     **(theme or {})}
    return tous


def univers_complet(config: dict[str, Any]) -> list[str]:
    """Les valeurs de tous les thèmes, sans doublon, dans l'ordre."""
    return list(dict.fromkeys(t for theme in themes(config).values() for t in theme["univers"]))


def theme_de(config: dict[str, Any]) -> dict[str, str]:
    """Ticker → libellé de son thème (le premier qui le cite)."""
    resultat: dict[str, str] = {}
    for theme in themes(config).values():
        for t in theme["univers"]:
            resultat.setdefault(t, theme["libelle"])
    return resultat


def tickers_suivis(config: dict[str, Any]) -> list[str]:
    """Portefeuille + univers de tous les thèmes + référence, sans doublon, dans l'ordre."""
    tickers = [p["ticker"] for p in config["portefeuille"]]
    tickers += univers_complet(config)
    tickers.append(config["reference"])
    return list(dict.fromkeys(tickers))
