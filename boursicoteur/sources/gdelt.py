"""GDELT : repérer un sujet qui monte dans la presse mondiale avant qu'il touche les cours.

API DOC 2.0 de GDELT (gdeltproject.org), gratuite et sans clé : nombre
d'articles par tranche de temps sur trois jours, dans toutes les langues.
Quand un sujet suivi (« Strait of Hormuz », « tariffs », le nom d'une de tes
sociétés…) est soudain bien plus couvert que d'habitude, ses articles les plus
récents deviennent un événement, relié aux valeurs suivies par evenements.py
comme les autres actus non boursières.

GDELT demande au plus une requête toutes les 5 secondes : chaque passage ne
lit que quelques sujets, à tour de rôle.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

import requests

from .actus import Actu

log = logging.getLogger(__name__)

URL = "https://api.gdeltproject.org/api/v2/doc/doc"
AGENT = {"User-Agent": "Boursicoteur (veille personnelle)"}
PAUSE_S = 5.5


def _get(params: dict) -> dict | None:
    try:
        reponse = requests.get(URL, params={**params, "format": "json"}, headers=AGENT, timeout=30)
        reponse.raise_for_status()
        return reponse.json()
    except (requests.RequestException, ValueError) as exc:
        # GDELT répond en texte brut quand on va trop vite ou qu'une requête est mal formée
        log.warning("GDELT injoignable ou réponse illisible : %s", type(exc).__name__)
        return None


def volumes(sujet: str) -> list[tuple[datetime, int]]:
    """Nombre d'articles par tranche de temps sur les trois derniers jours."""
    donnees = _get({"query": sujet, "mode": "timelinevolraw", "timespan": "72h"})
    serie = ((donnees or {}).get("timeline") or [{}])[0].get("data", [])
    points = []
    for p in serie:
        try:
            moment = datetime.strptime(p["date"], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            points.append((moment, int(p["value"])))
        except (KeyError, ValueError, TypeError):
            continue
    return points


def pic(points: list[tuple[datetime, int]], fenetre_h: float, facteur: float, minimum: int,
        maintenant: datetime | None = None) -> tuple[int, float] | None:
    """(articles des dernières heures, multiple de l'habitude) si le sujet est
    au moins ``facteur`` fois plus couvert que sa moyenne des jours précédents,
    avec au moins ``minimum`` articles. Indépendant de la taille des tranches."""
    if not points:
        return None
    maintenant = maintenant or datetime.now(timezone.utc)
    debut = maintenant - timedelta(hours=fenetre_h)
    recent = sum(v for m, v in points if m >= debut)
    anciens = [(m, v) for m, v in points if m < debut]
    if not anciens or recent < minimum:
        return None
    duree_h = (debut - min(m for m, _ in anciens)).total_seconds() / 3600
    if duree_h < fenetre_h:
        return None  # pas assez d'historique pour savoir ce qui est habituel
    habituel = sum(v for _, v in anciens) / duree_h * fenetre_h
    multiple = recent / max(habituel, 1)
    return (recent, multiple) if multiple >= facteur else None


def articles(sujet: str, fenetre_h: float, nombre: int = 5) -> list[dict]:
    donnees = _get({"query": sujet, "mode": "artlist", "maxrecords": nombre, "sort": "hybridrel",
                    "timespan": f"{max(1, round(fenetre_h))}h"})
    return [a for a in (donnees or {}).get("articles", []) if a.get("url") and a.get("title")]


def sujets_en_hausse(sujets: list[str], etat: dict, reglages: dict, pause=time.sleep) -> list[Actu]:
    """Lit ``reglages["par_passage"]`` sujets, à tour de rôle, et renvoie un
    événement par sujet en forte hausse. Un même sujet n'est signalé qu'une fois
    par ``reglages["repos_h"]`` heures. ``etat`` garde le tour et les sujets déjà signalés."""
    if not sujets:
        return []
    tour = etat.get("tour", 0) % len(sujets)
    a_lire = [sujets[(tour + i) % len(sujets)] for i in range(min(reglages["par_passage"], len(sujets)))]
    etat["tour"] = tour + len(a_lire)
    signales = etat.setdefault("signales", {})
    maintenant = datetime.now(timezone.utc)
    evenements = []
    for i, sujet in enumerate(a_lire):
        if maintenant - signales.get(sujet, datetime.min.replace(tzinfo=timezone.utc)) \
                < timedelta(hours=reglages["repos_h"]):
            continue
        if i:
            pause(PAUSE_S)
        hausse = pic(volumes(sujet), reglages["fenetre_h"], reglages["facteur"], reglages["minimum"], maintenant)
        if not hausse:
            continue
        pause(PAUSE_S)
        trouves = articles(sujet, reglages["fenetre_h"])
        if not trouves:
            continue
        signales[sujet] = maintenant
        recent, multiple = hausse
        titres = " | ".join(a["title"].strip() for a in trouves)
        evenements.append(Actu(
            titre=f"Sujet en forte hausse dans la presse mondiale : {sujet} "
                  f"({recent} articles en {reglages['fenetre_h']:g} h, {multiple:.1f} fois l'habitude)",
            lien=trouves[0]["url"],
            source=f"GDELT, {trouves[0].get('domain', '?')}",
            publie=trouves[0].get("seendate", ""),
            resume=titres[:900],
        ))
    return evenements
