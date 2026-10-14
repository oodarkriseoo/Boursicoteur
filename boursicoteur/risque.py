"""Une baisse est-elle normale pour cette valeur, ou inhabituelle ?

Chaque valeur a sa respiration : une action IA recule souvent de 10 % avant
de repartir, un ETF MSCI World rarement de plus de 5 %. Plutôt qu'un seuil
fixe, on compare la baisse en cours à l'historique de la valeur elle-même :

- profondeur du repli depuis le dernier sommet, comparée à tous les replis
  des deux dernières années (centile) ;
- chute du jour mesurée en écarts-types des variations habituelles ;
- structure de tendance : tant que chaque creux est plus haut que le
  précédent, la hausse est intacte ; enfoncer le dernier creux la casse ;
- passage sous la moyenne mobile 200 jours, la tendance de fond ;
- volatilité qui s'emballe par rapport à son niveau normal.

Ces signaux ne prédisent pas l'avenir : ils disent quand une baisse sort de
l'ordinaire, ce qui précède plus souvent une baisse prolongée qu'un repli
banal. Le score sur 100 sert à hiérarchiser, pas à garantir.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

from . import indicateurs as ind


def replis_passes(cloture: pd.Series) -> list[float]:
    """Profondeur (en %, négative) de chaque repli terminé : du sommet au
    point bas, jusqu'à ce qu'un nouveau sommet soit atteint. Le repli en cours
    n'est pas compté."""
    replis, sommet, creux = [], None, 0.0
    for cours in cloture:
        if sommet is None or cours >= sommet:
            if creux < 0:
                replis.append(creux)
            sommet, creux = cours, 0.0
        else:
            creux = min(creux, (cours / sommet - 1) * 100)
    return replis


def centile(valeur: float, echantillon: list[float]) -> float | None:
    """Part (en %) des replis passés moins profonds que ``valeur``."""
    if len(echantillon) < 5:
        return None
    return 100 * sum(1 for r in echantillon if r > valeur) / len(echantillon)


def ecart_type_jour(hist: pd.DataFrame) -> float | None:
    """Écart-type des variations quotidiennes (en %) sur un an, hors dernier jour."""
    variations = hist["Close"].dropna().pct_change().dropna() * 100
    if len(variations) < 60:
        return None
    sigma = float(variations.iloc[-251:-1].std())
    return sigma if sigma and not math.isnan(sigma) else None


def creux_confirmes(cloture: pd.Series, demi_fenetre: int = 5) -> list[float]:
    """Points bas locaux : plus bas que les ``demi_fenetre`` séances de part et d'autre."""
    valeurs = list(cloture)
    creux = []
    for i in range(demi_fenetre, len(valeurs) - demi_fenetre):
        fenetre = valeurs[i - demi_fenetre : i + demi_fenetre + 1]
        if valeurs[i] == min(fenetre):
            creux.append(valeurs[i])
    return creux


@dataclass
class Risque:
    ticker: str
    score: int = 0
    raisons: list[str] = field(default_factory=list)
    repli_pct: float = 0.0
    centile_repli: float | None = None
    repli_habituel_pct: float | None = None  # médiane des replis passés
    z_jour: float | None = None
    structure_cassee: bool = False


def evaluer(ticker: str, hist: pd.DataFrame) -> Risque | None:
    cloture = hist["Close"].dropna()
    if len(cloture) < 60:
        return None
    r = Risque(ticker)
    points = 0
    dernier = float(cloture.iloc[-1])

    # 1. Profondeur du repli en cours comparée aux replis habituels
    r.repli_pct = (dernier / float(cloture.iloc[-252:].max()) - 1) * 100
    passes = replis_passes(cloture.iloc[:-1])
    passes = [p for p in passes if p <= -1]  # on ignore les micro-replis
    if passes:
        r.repli_habituel_pct = float(pd.Series(passes).median())
    r.centile_repli = centile(r.repli_pct, passes)
    if r.centile_repli is not None and r.repli_pct < -1:
        if r.centile_repli >= 90:
            points += 35
            r.raisons.append(
                f"repli de {r.repli_pct:.1f} % depuis le sommet, plus profond que "
                f"{r.centile_repli:.0f} % de ses replis passés (habituel : {r.repli_habituel_pct:.1f} %)"
            )
        elif r.centile_repli >= 75:
            points += 20
            r.raisons.append(
                f"repli de {r.repli_pct:.1f} %, plus profond que {r.centile_repli:.0f} % de ses replis passés"
            )

    # 2. Chute du jour en écarts-types
    sigma = ecart_type_jour(hist)
    seance = ind.variation_seance_pct(hist)
    if sigma and seance is not None:
        r.z_jour = seance / sigma
        if r.z_jour <= -3:
            points += 25
            r.raisons.append(f"chute du jour {seance:.1f} %, soit {-r.z_jour:.1f} fois sa variation normale")
        elif r.z_jour <= -2:
            points += 12
            r.raisons.append(f"chute du jour {seance:.1f} %, {-r.z_jour:.1f} fois sa variation normale")

    # 3. Structure : creux montants puis dernier creux enfoncé
    creux = creux_confirmes(cloture.iloc[-130:])
    if len(creux) >= 2 and creux[-1] > creux[-2] and dernier < creux[-1]:
        r.structure_cassee = True
        points += 20
        r.raisons.append(f"le dernier creux ({creux[-1]:.2f}) est enfoncé : la série de creux montants s'interrompt")

    # 4. Tendance de fond
    mm200 = ind.moyenne_mobile(hist, 200)
    if mm200 is not None and dernier < mm200:
        recemment_au_dessus = len(cloture) > 210 and float(cloture.iloc[-10]) > mm200
        points += 15 if recemment_au_dessus else 8
        r.raisons.append("passage sous la moyenne 200 jours" if recemment_au_dessus
                         else "sous la moyenne 200 jours")

    # 5. Volatilité qui s'emballe
    variations = cloture.pct_change().dropna()
    if sigma and len(variations) >= 20:
        ratio = float(variations.iloc[-20:].std() * 100) / sigma
        if ratio >= 1.5:
            points += 10
            r.raisons.append(f"volatilité {ratio:.1f} fois plus forte que d'habitude")

    r.score = min(100, points)
    return r


def effet_sur_gains(cloture: pd.Series, prix_revient: float | None) -> str | None:
    """Situe la baisse par rapport aux gains déjà faits."""
    if not prix_revient:
        return None
    dernier = float(cloture.iloc[-1])
    sommet = float(cloture.iloc[-252:].max())
    gain = (dernier / prix_revient - 1) * 100
    gain_max = (sommet / prix_revient - 1) * 100
    if gain_max <= 0:
        return f"Tu es à {gain:+.1f} % de ton prix de revient, sans avoir été en gain sur un an."
    efface = (1 - (dernier - prix_revient) / (sommet - prix_revient)) * 100
    if gain > 0:
        return (f"Tu restes à {gain:+.1f} % sur ton prix de revient ; ce repli a effacé "
                f"{efface:.0f} % de ton gain maximal ({gain_max:+.1f} %).")
    return f"Tout ton gain ({gain_max:+.1f} % au sommet) est effacé : tu es à {gain:+.1f} %."


def formater(r: Risque, nom: str, phrase_gains: str | None) -> str:
    niveau = "élevé" if r.score >= 70 else "à surveiller"
    lignes = [f"🌊 Baisse inhabituelle sur {nom} ({r.ticker}) : risque {r.score}/100, {niveau}"]
    lignes += [f"• {raison}" for raison in r.raisons]
    if phrase_gains:
        lignes.append(phrase_gains)
    lignes.append("Ce n'est pas une prédiction : c'est une baisse qui sort de l'ordinaire pour cette valeur. "
                  "Si tu veux protéger ton gain, alléger d'un tiers est un compromis.")
    return "\n".join(lignes)
