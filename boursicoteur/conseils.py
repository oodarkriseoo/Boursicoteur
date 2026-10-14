"""Phase 2 : conseils d'achat et de vente chiffrés, à exécuter à la main.

Un conseil d'achat donne un prix maximum, un stop, un objectif et un montant
calculé pour que, si le stop est touché, la perte reste une petite part de la
poche dédiée (règle du risque fixe par position). Un avis de vente sur chaque
position dit quoi faire : garder, alléger, prendre une partie des gains ou
vendre, avec la raison.

Chaque conseil d'achat est suivi au journal jusqu'à son stop ou son objectif :
le bilan dit si ces conseils gagnent plus qu'ils ne perdent, frais compris.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from . import indicateurs as ind


def mouvement_moyen(hist: pd.DataFrame, jours: int = 14) -> float | None:
    """Variation absolue moyenne d'une clôture à l'autre : l'amplitude normale
    d'une journée pour cette valeur (proche de l'ATR, sans plus haut ni plus bas)."""
    cloture = hist["Close"].dropna()
    if len(cloture) <= jours:
        return None
    return float(cloture.diff().abs().iloc[-jours:].mean())


SUFFIXES_EURO = {"PA", "AS", "DE", "MI", "MC", "BR", "LS", "F", "VI", "HE", "IR"}
# Places de l'Union européenne et de l'EEE hors zone euro : éligibles au PEA
# (Stockholm, Copenhague, Oslo, Varsovie, Prague, Budapest). Pas Londres ni Zurich.
SUFFIXES_PEA = SUFFIXES_EURO | {"ST", "CO", "OL", "WA", "PR", "BD"}


def devise_de(ticker: str) -> str:
    """Devise de cotation déduite du ticker Yahoo : euro pour les places de la zone euro."""
    if ticker.endswith("-EUR"):
        return "EUR"
    if "." in ticker and ticker.rsplit(".", 1)[1] in SUFFIXES_EURO:
        return "EUR"
    return "USD"


def enveloppe_de(ticker: str) -> str:
    """Indication seulement : l'éligibilité au PEA se vérifie chez le courtier."""
    if "." in ticker and ticker.rsplit(".", 1)[1] in SUFFIXES_PEA:
        return "PEA possible"
    return "compte-titres"


@dataclass
class PlanAchat:
    ticker: str
    cours: float
    prix_max: float  # ordre à cours limité : ne pas courir après le titre
    stop: float
    objectif: float
    quantite: float
    montant_eur: float
    risque_eur: float  # perte si le stop est touché, frais compris
    devise: str = "EUR"

    def formater(self) -> str:
        d = " €" if self.devise == "EUR" else f" {self.devise}"
        ecart_stop = (self.stop / self.cours - 1) * 100
        ecart_obj = (self.objectif / self.cours - 1) * 100
        return (
            f"🟢 Conseil d'achat {self.ticker} ({enveloppe_de(self.ticker)})\n"
            f"• Ordre à cours limité, au plus {self.prix_max:.2f}{d} (cours {self.cours:.2f}{d})\n"
            f"• Quantité : {self.quantite:g}, soit environ {self.montant_eur:.0f} €\n"
            f"• Stop : {self.stop:.2f}{d} ({ecart_stop:+.1f} %), perte maximale ≈ "
            f"{self.risque_eur:.0f} € frais compris\n"
            f"• Objectif : {self.objectif:.2f}{d} ({ecart_obj:+.1f} %) : vends au moins la moitié là\n"
            "Si le cours dépasse le prix maximum avant ton ordre, laisse passer. "
            "Je te préviens quand le stop ou l'objectif est atteint."
        )


def plan_achat(ticker: str, hist: pd.DataFrame, reglages: dict[str, Any],
               taux: float = 1.0, fractionnable: bool = True) -> PlanAchat | None:
    """Stop à 2,5 mouvements moyens sous le cours (entre 4 et 15 %), objectif
    à ``ratio_objectif`` fois la distance au stop, taille fixée par le risque.
    ``taux`` : combien vaut 1 € dans la devise de cotation (1 pour l'euro)."""
    cloture = hist["Close"].dropna()
    amplitude = mouvement_moyen(hist)
    if cloture.empty or not amplitude:
        return None
    cours = float(cloture.iloc[-1])
    distance = min(max(2.5 * amplitude / cours, 0.04), 0.15)
    stop = cours * (1 - distance)
    objectif = cours * (1 + reglages["ratio_objectif"] * distance)

    poche = reglages["poche_eur"]
    frais = 2 * reglages["frais_ordre_eur"]  # achat puis vente
    risque_vise = poche * reglages["risque_par_position_pct"] / 100 - frais
    montant = min(risque_vise / distance, poche * reglages["position_max_pct"] / 100)
    if montant <= 0:
        return None
    quantite = montant * taux / cours
    quantite = round(quantite, 2) if fractionnable else float(int(quantite))
    if quantite <= 0:
        return None  # une seule action coûte plus que la position maximale
    montant = quantite * cours / taux
    return PlanAchat(ticker, cours, cours * 1.01, stop, objectif, quantite, montant,
                     montant * distance + frais, devise_de(ticker))


@dataclass
class Avis:
    ticker: str
    action: str  # garder, alléger, prendre des gains, vendre, réévaluer (long terme)
    raisons: list[str] = field(default_factory=list)
    stop_suiveur: float | None = None

    @property
    def emoji(self) -> str:
        return {"vendre": "🔴", "alléger": "🟠", "prendre des gains": "💰",
                "réévaluer": "🟡"}.get(self.action, "🟢")


def horizon_de(position: dict[str, Any]) -> str:
    """« long » pour une position de fond (par défaut celles du PEA), « court » sinon."""
    return position.get("horizon") or ("long" if position.get("enveloppe") == "PEA" else "court")


def avis_position(position: dict[str, Any], hist: pd.DataFrame,
                  prix_revient: float | None) -> Avis | None:
    """Avis sur une position détenue. Pour une position de long terme, la
    règle de trading (stop suiveur, tendance) ne commande pas la vente : elle
    invite à réévaluer la raison de l'achat.
    """
    avis = _avis_technique(position, hist, prix_revient)
    if avis and horizon_de(position) == "long" and avis.action in ("vendre", "alléger"):
        avis.action = "réévaluer"
        avis.raisons.append("position de long terme : la raison de ton achat tient-elle toujours ?")
    return avis


def _avis_technique(position: dict[str, Any], hist: pd.DataFrame,
                    prix_revient: float | None) -> Avis | None:
    """Avis selon les règles de trading. Le stop suiveur (plus haut de 3 mois moins
    3 mouvements moyens) protège les gains : il monte avec le cours, jamais l'inverse."""
    cloture = hist["Close"].dropna()
    amplitude = mouvement_moyen(hist)
    if len(cloture) < 50 or not amplitude:
        return None
    ticker = position["ticker"]
    cours = float(cloture.iloc[-1])
    stop_suiveur = float(cloture.iloc[-63:].max()) - 3 * amplitude
    if position.get("stop"):
        stop_suiveur = max(stop_suiveur, float(position["stop"]))
    gain = (cours / prix_revient - 1) * 100 if prix_revient else None
    sens = ind.tendance(hist)
    niveau_rsi = ind.rsi(hist)
    mm50 = ind.moyenne_mobile(hist, 50)

    if cours <= stop_suiveur and sens == "baissière":
        return Avis(ticker, "vendre", [
            f"cours {cours:.2f} sous le stop suiveur {stop_suiveur:.2f}",
            "tendance baissière (cours < MM20 < MM50)",
        ], stop_suiveur)
    if gain is not None and gain >= 30 and niveau_rsi is not None and niveau_rsi >= 75:
        return Avis(ticker, "prendre des gains", [
            f"+{gain:.0f} % sur ton prix de revient", f"RSI {niveau_rsi:.0f}, surachat",
            "vendre un tiers sécurise une partie du gain",
        ], stop_suiveur)
    if sens == "baissière" and mm50 and cours < mm50:
        raisons = ["tendance baissière (cours < MM20 < MM50)"]
        if gain is not None:
            raisons.append(f"{gain:+.0f} % sur ton prix de revient")
        return Avis(ticker, "alléger", raisons, stop_suiveur)
    raisons = [f"tendance {sens}"]
    if gain is not None:
        raisons.append(f"{gain:+.0f} % sur ton prix de revient")
    return Avis(ticker, "garder", raisons, stop_suiveur)


def formater_avis(avis: Avis, nom: str) -> str:
    texte = f"{avis.emoji} {nom} ({avis.ticker}) : {avis.action}. {', '.join(avis.raisons)}."
    if avis.stop_suiveur and avis.action not in ("vendre", "réévaluer"):
        texte += f" Sortie si clôture sous {avis.stop_suiveur:.2f}."
    return texte
