"""Alertes de risque sur le portefeuille et détection de pistes sur le thème IA."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from . import indicateurs as ind
from . import risque as rsq
from .sources.actus import Actu, mentionne


@dataclass(frozen=True)
class Alerte:
    cle: str  # sert à ne pas renvoyer la même alerte deux fois dans la journée
    message: str
    urgente: bool = True
    # True : envoyée une seule fois tant que la clé ne change pas (et non
    # chaque jour), pour les situations qui durent comme une perte latente.
    unique: bool = False


@dataclass
class Piste:
    ticker: str
    score: float
    raisons: list[str] = field(default_factory=list)
    tendance: str = "neutre"
    variation_1m_pct: float | None = None
    dernier_cours: float | None = None


def alertes_portefeuille(
    portefeuille: list[dict[str, Any]],
    historiques: dict[str, pd.DataFrame],
    seuils: dict[str, float],
    taux_change: dict[str, float] | None = None,
    cours_precedents: dict[str, float] | None = None,
    titres: dict[str, Actu] | None = None,
) -> list[Alerte]:
    """``taux_change`` donne, pour chaque devise, combien vaut 1 € (ex. USD : 1,15).
    Il sert aux positions cotées hors euro (champ ``devise`` de la position),
    dont le prix de revient est saisi en euros.

    ``cours_precedents`` : cours relevés au contrôle précédent, pour repérer
    une chute rapide en cours de séance. ``titres`` : dernière actu connue par
    valeur, jointe aux alertes de chute pour en comprendre la cause."""
    taux_change = taux_change or {}
    cours_precedents = cours_precedents or {}
    titres = titres or {}
    alertes: list[Alerte] = []
    for position in portefeuille:
        ticker = position["ticker"]
        nom = position.get("nom", ticker)
        hist = historiques.get(ticker)
        if hist is None or hist["Close"].dropna().empty:
            continue
        dernier = float(hist["Close"].dropna().iloc[-1])

        prix_revient = position.get("prix_revient")
        devise = position.get("devise", "EUR")
        if prix_revient and devise != "EUR":
            # prix de revient en euros converti dans la devise de cotation
            taux = taux_change.get(devise)
            prix_revient = prix_revient * taux if taux else None
        details = _details_chute(hist, dernier, prix_revient, titres.get(ticker))
        # baisse jugée à l'aune de la respiration habituelle de la valeur
        risque = rsq.evaluer(ticker, hist)
        sigma = rsq.ecart_type_jour(hist)

        seance = ind.variation_seance_pct(hist)
        seance_ordinaire = risque is not None and risque.z_jour is not None and risque.z_jour > -2
        if (seance is not None and seance <= -seuils["baisse_seance_pct"]
                and (not seance_ordinaire or seance <= -2 * seuils["baisse_seance_pct"])):
            # nouvelle alerte dans la journée à chaque palier de 3 points
            alertes.append(
                Alerte(
                    f"seance:{ticker}:{palier(seance, 3)}",
                    f"📉 {nom} ({ticker}) perd {seance:.1f} % sur la séance.\n{details}",
                )
            )

        precedent = cours_precedents.get(ticker)
        if precedent:
            chute = (dernier / precedent - 1) * 100
            if chute <= -max(seuils["chute_rapide_pct"], 1.5 * (sigma or 0)):
                alertes.append(
                    Alerte(
                        f"rapide:{ticker}:{dernier:.6g}",
                        f"🚨 Chute rapide : {nom} ({ticker}) a perdu {-chute:.1f} % depuis le "
                        f"dernier contrôle ({precedent:.2f} → {dernier:.2f}).\n{details}",
                    )
                )

        stop = position.get("stop")
        if stop and dernier <= stop:
            alertes.append(
                Alerte(
                    f"stop:{ticker}",
                    f"⛔ {nom} ({ticker}) est passé sous ton seuil de sortie {stop:.2f} "
                    f"(cours {dernier:.2f}). C'est le moment d'appliquer la règle que tu t'étais fixée.\n{details}",
                )
            )

        if risque is not None and risque.score >= seuils["risque_alerte"]:
            alertes.append(
                Alerte(
                    f"risque:{ticker}:{palier(-risque.score, 10)}",
                    rsq.formater(risque, nom, rsq.effet_sur_gains(hist["Close"].dropna(), prix_revient)),
                )
            )

        sommet = ind.ecart_au_plus_haut_pct(hist.iloc[-63:])
        repli_habituel = (risque is not None and risque.centile_repli is not None
                          and risque.centile_repli < 75)
        if sommet is not None and sommet <= -seuils["baisse_depuis_sommet_pct"] and not repli_habituel:
            alertes.append(
                Alerte(
                    f"sommet:{ticker}:{palier(sommet, 5)}",
                    f"⚠️ {nom} ({ticker}) est à {sommet:.1f} % de son plus haut sur 3 mois.",
                    unique=True,
                )
            )

        if prix_revient:
            perte = (dernier / prix_revient - 1) * 100
            if perte <= -seuils["perte_vs_prix_revient_pct"]:
                alertes.append(
                    Alerte(
                        f"revient:{ticker}:{palier(perte, 10)}",
                        f"🛑 {nom} ({ticker}) est à {perte:.1f} % de ton prix de revient "
                        f"({dernier:.2f} contre {prix_revient:.2f}). "
                        "C'est le moment de relire la règle de sortie que tu t'étais fixée.",
                        unique=True,
                    )
                )
    return alertes


def _details_chute(hist: pd.DataFrame, dernier: float, prix_revient: float | None,
                   actu: Actu | None) -> str:
    """Ce qui aide à décider vite : où tu en es, le support proche, la cause probable."""
    lignes = []
    if prix_revient:
        lignes.append(f"Par rapport à ton prix de revient : {(dernier / prix_revient - 1) * 100:+.1f} %.")
    clotures = hist["Close"].dropna()
    if len(clotures) > 20:
        plus_bas = float(clotures.iloc[-21:-1].min())
        if dernier > plus_bas:
            lignes.append(f"Plus bas des 20 dernières séances : {plus_bas:.2f}. "
                          "S'il casse, la baisse a plus de chances de continuer.")
        else:
            lignes.append(f"Le cours est sous son plus bas des 20 dernières séances ({plus_bas:.2f}).")
    if actu is not None:
        lignes.append(f"Dernière actu : {actu.titre}\n{actu.lien}")
    return "\n".join(lignes)


def palier(baisse_pct: float, pas: int) -> int:
    """Palier de baisse franchi (-23 % avec un pas de 10 donne 20) : une
    nouvelle alerte part seulement quand la baisse s'aggrave d'un palier."""
    return int(-baisse_pct // pas) * pas


def alerte_marche(vix: pd.DataFrame | None, seuil: float) -> list[Alerte]:
    if vix is None or vix.empty:
        return []
    niveau = float(vix["Close"].dropna().iloc[-1])
    if niveau >= seuil:
        return [
            Alerte(
                "vix",
                f"🌪️ Le VIX (indice de peur) est à {niveau:.1f}, au-dessus de {seuil:.0f}. "
                "Les marchés sont nerveux : évite les décisions précipitées.",
            )
        ]
    return []


def scanner_theme_ia(
    univers: list[str],
    historiques: dict[str, pd.DataFrame],
    actus: list[Actu],
    mots_cles: list[str],
    seuil_volume: float,
    pistes_max: int = 5,
) -> list[Piste]:
    """Classe les valeurs de l'univers selon des signaux concrets.

    Le score additionne : dynamique sur 1 et 3 mois, tendance, volume
    inhabituel et nombre d'actus récentes sur le thème. Un RSI très élevé
    (surachat) retire des points : monter vite rend aussi plus fragile.
    """
    pistes: list[Piste] = []
    for ticker in univers:
        hist = historiques.get(ticker)
        if hist is None or len(hist) < 25:
            continue
        raisons: list[str] = []
        score = 0.0

        v1m = ind.variation_sur_pct(hist, 21)
        v3m = ind.variation_sur_pct(hist, 63)
        if v1m is not None:
            score += max(min(v1m, 40), -40) / 10
            if v1m >= 10:
                raisons.append(f"+{v1m:.0f} % sur 1 mois")
        if v3m is not None and v3m >= 25:
            score += 1
            raisons.append(f"+{v3m:.0f} % sur 3 mois")

        sens = ind.tendance(hist)
        if sens == "haussière":
            score += 1
            raisons.append("tendance haussière (cours > MM20 > MM50)")
        elif sens == "baissière":
            score -= 1

        volume = ind.ratio_volume(hist)
        if volume is not None and volume >= seuil_volume:
            score += 1.5
            raisons.append(f"volume {volume:.1f}× la moyenne")

        nb_actus = sum(1 for a in actus if a.ticker == ticker and mentionne(a, mots_cles))
        if nb_actus:
            score += min(nb_actus, 5) * 0.5
            raisons.append(f"{nb_actus} actu(s) récente(s) sur l'IA")

        niveau_rsi = ind.rsi(hist)
        if niveau_rsi is not None and niveau_rsi >= 75:
            score -= 1
            raisons.append(f"attention, RSI {niveau_rsi:.0f} (surachat)")

        pistes.append(
            Piste(
                ticker=ticker,
                score=round(score, 2),
                raisons=raisons,
                tendance=sens,
                variation_1m_pct=v1m,
                dernier_cours=float(hist["Close"].dropna().iloc[-1]),
            )
        )
    pistes.sort(key=lambda p: p.score, reverse=True)
    return pistes[:pistes_max]
