"""Garde-fous sur l'ensemble du portefeuille : stop suiveur par ligne, poids
maximal d'un seul titre, recul du portefeuille depuis son plus haut et
éligibilité des lignes notées au PEA.

Les alertes de chute de signaux.py regardent chaque valeur isolément ; ici on
regarde ce que chaque ligne pèse dans ton argent total.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd

from . import conseils as cons
from . import portefeuille as ptf
from .signaux import Alerte, palier


def _clotures(hist: pd.DataFrame | None) -> pd.Series | None:
    if hist is None or "Close" not in hist:
        return None
    cloture = hist["Close"].dropna()
    if cloture.empty:
        return None
    index = pd.DatetimeIndex(cloture.index)
    if index.tz is not None:
        index = index.tz_localize(None)
    cloture.index = index.normalize()
    return cloture.groupby(level=0).last()


def _taux(position: dict[str, Any], taux_change: dict[str, float]) -> float | None:
    """Combien vaut 1 € dans la devise de cotation de la position."""
    devise = position.get("devise", "EUR")
    return 1.0 if devise == "EUR" else taux_change.get(devise)


def depassements(etat: ptf.Etat | None, portefeuille: list[dict[str, Any]],
                 plafond_pct: float) -> list[tuple[ptf.Ligne, float]]:
    """Lignes au-dessus du plafond, avec le montant à alléger pour y revenir.
    Les ETF (``diversifie: true``) sont exemptés : leur poids ne concentre pas
    le risque sur une seule entreprise."""
    if etat is None or len(etat.lignes) < 2:
        return []
    diversifies = {p["ticker"] for p in portefeuille if p.get("diversifie")}
    plafond = plafond_pct / 100
    # vendre x ramène la ligne au plafond : (v - x) / (total - x) = plafond
    return [(l, (l.valeur_eur - plafond * etat.valeur_eur) / (1 - plafond)) for l in etat.lignes
            if l.ticker not in diversifies and l.poids_pct > plafond_pct]


def alertes_concentration(etat: ptf.Etat | None, portefeuille: list[dict[str, Any]],
                          plafond_pct: float) -> list[Alerte]:
    """Un seul titre au-dessus du plafond : une mauvaise nouvelle sur lui pèse
    sur tout le portefeuille."""
    return [Alerte(
        f"concentration:{l.ticker}:{palier(-l.poids_pct, 5)}",
        f"⚖️ {l.nom} ({l.ticker}) pèse {l.poids_pct:.0f} % de ton portefeuille "
        f"(plafond {plafond_pct:.0f} %). Une mauvaise nouvelle sur ce seul titre te coûterait cher. "
        f"Évite de renforcer ; pour revenir au plafond, il faudrait alléger d'environ {a_vendre:.0f} €.",
        urgente=False, unique=True,
    ) for l, a_vendre in depassements(etat, portefeuille, plafond_pct)]


@dataclass
class Suiveur:
    ticker: str
    nom: str
    sommet: float
    date_sommet: date
    niveau: float  # sortie : sommet moins suiveur_pct
    cours: float

    @property
    def touche(self) -> bool:
        return self.cours <= self.niveau


def stops_suiveurs(portefeuille: list[dict[str, Any]],
                   historiques: dict[str, pd.DataFrame]) -> list[Suiveur]:
    """Pour chaque ligne avec ``suiveur_pct`` : niveau de sortie qui monte avec
    le plus haut atteint depuis l'achat (``achat_le``), ou sur 6 mois sans date."""
    resultat = []
    for p in portefeuille:
        pct = p.get("suiveur_pct")
        cloture = _clotures(historiques.get(p["ticker"]))
        if not pct or cloture is None:
            continue
        if p.get("achat_le"):
            fenetre = cloture[cloture.index >= pd.Timestamp(str(p["achat_le"]))]
        else:
            fenetre = cloture.iloc[-126:]
        if fenetre.empty:
            continue
        sommet = float(fenetre.max())
        resultat.append(Suiveur(p["ticker"], p.get("nom", p["ticker"]), sommet,
                                fenetre.idxmax().date(), sommet * (1 - pct / 100), float(cloture.iloc[-1])))
    return resultat


def alertes_suiveurs(suiveurs: list[Suiveur]) -> list[Alerte]:
    return [Alerte(
        # un nouveau plus haut donne un nouveau niveau, donc une nouvelle alerte possible
        f"suiveur:{s.ticker}:{s.sommet:.6g}",
        f"🪜 Stop suiveur touché : {s.nom} ({s.ticker}) est à {s.cours:.2f}, sous ton niveau de sortie "
        f"{s.niveau:.2f} ({(s.cours / s.sommet - 1) * 100:.1f} % depuis son plus haut {s.sommet:.2f} "
        f"du {s.date_sommet:%d/%m/%Y}). C'est la règle que tu t'étais fixée pour protéger tes gains.",
        unique=True,
    ) for s in suiveurs if s.touche]


def valeur_historique(portefeuille: list[dict[str, Any]], historiques: dict[str, pd.DataFrame],
                      taux_change: dict[str, float]) -> pd.Series | None:
    """Valeur du portefeuille en euros, jour par jour, avec les quantités
    actuelles. Le taux de change suit l'historique EUR<devise>=X s'il est
    disponible, sinon le taux du jour."""
    series = []
    for p in portefeuille:
        cloture = _clotures(historiques.get(p["ticker"]))
        taux = _taux(p, taux_change)
        if cloture is None or not taux or not p.get("quantite"):
            continue
        devise = p.get("devise", "EUR")
        change = _clotures(historiques.get(f"EUR{devise}=X")) if devise != "EUR" else None
        if change is not None:
            change = change.reindex(cloture.index, method="ffill").fillna(taux)
        else:
            change = taux
        series.append(p["quantite"] * cloture / change)
    if not series:
        return None
    # places et jours fériés différents : on reporte le dernier cours connu
    tableau = pd.concat(series, axis=1).sort_index().ffill().dropna()
    return tableau.sum(axis=1) if not tableau.empty else None


@dataclass
class Recul:
    pct: float
    sommet_eur: float
    date_sommet: date
    valeur_eur: float


def recul(valeur: pd.Series | None, jours: int = 252) -> Recul | None:
    """Recul du portefeuille depuis son plus haut sur un an (drawdown)."""
    if valeur is None or valeur.empty:
        return None
    fenetre = valeur.iloc[-jours:]
    sommet = float(fenetre.max())
    actuelle = float(fenetre.iloc[-1])
    return Recul((actuelle / sommet - 1) * 100, sommet, fenetre.idxmax().date(), actuelle)


def alerte_recul(r: Recul | None, seuil_pct: float) -> list[Alerte]:
    if r is None or r.pct > -seuil_pct:
        return []
    return [Alerte(
        f"recul:{r.date_sommet}:{palier(r.pct, 5)}",
        f"📉 Ton portefeuille recule de {-r.pct:.1f} % depuis son plus haut "
        f"({r.valeur_eur:.0f} € contre {r.sommet_eur:.0f} € le {r.date_sommet:%d/%m/%Y}). "
        "Relis tes stops ligne par ligne et ne renforce pas dans la précipitation.",
        unique=True,
    )]


def verifier_pea(portefeuille: list[dict[str, Any]]) -> list[Alerte]:
    """Une ligne notée au PEA cotée hors de l'Union européenne ou de l'EEE n'y
    est pas éligible. Indication seulement : le courtier a le dernier mot,
    notamment pour les ETF."""
    alertes = []
    for p in portefeuille:
        if p.get("enveloppe") != "PEA" or cons.enveloppe_de(p["ticker"]) == "PEA possible":
            continue
        nom = p.get("nom", p["ticker"])
        alertes.append(Alerte(
            f"pea:{p['ticker']}",
            f"🏷️ {nom} ({p['ticker']}) est noté au PEA, mais sa cotation laisse penser qu'il n'y est pas "
            "éligible (société hors Union européenne). Vérifie chez ton courtier : un titre non éligible "
            "doit sortir du PEA, sinon le plan risque d'être clôturé. Si c'est une erreur de saisie, "
            "remets la ligne en CTO avec /ajouter puis retire-la du PEA avec /retirer TICKER PEA.",
            urgente=False, unique=True,
        ))
    return alertes


def bascules_pea(portefeuille: list[dict[str, Any]]) -> list[str]:
    """Lignes d'un compte-titres qui pourraient aller au PEA (fiscalité plus
    douce après 5 ans). Information pour un prochain achat, pas une alerte."""
    return [p.get("nom", p["ticker"]) for p in portefeuille
            if p.get("enveloppe") == "CTO" and cons.enveloppe_de(p["ticker"]) == "PEA possible"]


def synthese(conf: dict[str, Any], historiques: dict[str, pd.DataFrame],
             taux_change: dict[str, float], etat: ptf.Etat | None = None) -> list[str]:
    """Lignes « Garde-fous » du résumé quotidien. Le poids de chaque ligne est
    détaillé par le suivi du portefeuille : seuls les dépassements figurent ici."""
    portefeuille = conf["portefeuille"]
    if not portefeuille:
        return []
    seuils = conf["seuils"]
    sortie = []
    plafond = seuils["concentration_max_pct"]
    for l, a_vendre in depassements(etat, portefeuille, plafond):
        sortie.append(f"⚖️ {l.nom} pèse {l.poids_pct:.0f} % du portefeuille (plafond {plafond:.0f} %) : "
                      f"alléger d'environ {a_vendre:.0f} € pour y revenir")
    r = recul(valeur_historique(portefeuille, historiques, taux_change))
    if r is not None:
        sortie.append(f"📉 Recul depuis le plus haut sur un an : {r.pct:.1f} % "
                      f"(alerte à -{seuils['drawdown_alerte_pct']:.0f} %)")
    for s in stops_suiveurs(portefeuille, historiques):
        sortie.append(f"🪜 {s.nom} : stop suiveur {s.niveau:.2f} (plus haut {s.sommet:.2f}, "
                      f"cours {s.cours:.2f}){' : touché' if s.touche else ''}")
    if douteuses := [a.cle.split(":", 1)[1] for a in verifier_pea(portefeuille)]:
        sortie.append(f"🏷️ Notées au PEA mais sans doute non éligibles : {', '.join(douteuses)}. "
                      "Vérifie chez ton courtier.")
    if bascules := bascules_pea(portefeuille):
        sortie.append(f"🏷️ Éligibles au PEA mais sur le compte-titres : {', '.join(bascules)}. "
                      "Pour un prochain achat, le PEA est fiscalement plus doux après 5 ans.")
    return sortie


def alertes(conf: dict[str, Any], historiques: dict[str, pd.DataFrame],
            taux_change: dict[str, float], etat: ptf.Etat | None = None) -> list[Alerte]:
    """Alertes de la veille : stop suiveur, concentration, recul, éligibilité PEA."""
    portefeuille = conf["portefeuille"]
    seuils = conf["seuils"]
    resultat = alertes_suiveurs(stops_suiveurs(portefeuille, historiques))
    resultat += alertes_concentration(etat, portefeuille, seuils["concentration_max_pct"])
    resultat += alerte_recul(recul(valeur_historique(portefeuille, historiques, taux_change)),
                             seuils["drawdown_alerte_pct"])
    resultat += verifier_pea(portefeuille)
    return resultat
