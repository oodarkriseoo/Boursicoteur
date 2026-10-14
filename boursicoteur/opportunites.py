"""Opportunités scorées de 0 à 100 : un catalyseur dans l'actu, confirmé par le marché.

Le schéma recherché est celui de Nvidia ou Intel : une nouvelle concrète
(partenariat, contrat, prise de participation, résultats au-dessus des
attentes) tombe sur une valeur dont le cours commence à grimper avec des
volumes inhabituels. Le score additionne quatre composantes :

- catalyseur (0 à 40) : la force de la nouvelle ;
- dynamique (0 à 25) : hausse sur 1 mois et tendance ;
- volume (0 à 20) : les acheteurs suivent-ils ?
- accélération (0 à 15) : la séance du jour décolle-t-elle ?

Des points sont retirés pour une nouvelle négative, une tendance baissière
ou un RSI de surachat. Le catalyseur est noté par mots-clés, puis affiné par
Claude quand une clé d'API est disponible. Un score élevé n'est pas une
garantie : c'est une invitation à regarder de plus près.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from . import hypotheses as hyp
from . import indicateurs as ind
from .sources.actus import Actu

log = logging.getLogger(__name__)

# (poids, mots-clés) ; les titres Yahoo sont en anglais, la presse française aussi lue
CATALYSEURS: dict[str, tuple[int, list[str]]] = {
    "partenariat": (30, ["partnership", "partners with", "teams up", "collaborat", "alliance",
                         "agreement with", "deal with", "joint venture", "partenariat", "accord avec"]),
    "investissement": (30, ["invests", "investment in", "stake in", "takes stake", "to acquire",
                            "acquisition", "merger", "buyout", "prise de participation", "rachat"]),
    "contrat": (25, ["contract", "awarded", "wins order", "selected by", "chosen by", "to supply",
                     "supplier", "foundry", "to manufacture", "to build", "contrat", "commande"]),
    "résultats": (25, ["beats", "tops estimates", "record revenue", "raises guidance", "raises outlook",
                       "raises forecast", "raises price target", "upgrade", "relève ses prévisions"]),
    "lancement": (10, ["launch", "unveils", "new chip", "lancement"]),
}
NEGATIF = (-25, ["downgrade", "cuts guidance", "lowers guidance", "cuts forecast", "misses", "probe",
                 "investigation", "lawsuit", "export ban", "export curbs", "restrictions", "delays",
                 "recall", "share offering", "dilution", "abaisse", "enquête"])


CAS_MINIMUM = 10  # en dessous, l'historique est trop maigre pour corriger quoi que ce soit


def ajuster_poids(poids: int, effet: dict[str, float] | None) -> int:
    """Corrige le poids d'une catégorie de news par son effet mesuré : +5 points
    d'écart moyen à la référence sur une semaine multiplient le poids par 1,5,
    -5 points le divisent par deux. Bornes : de 0,5 à 1,5 fois le poids de départ."""
    if not effet or effet["cas"] < CAS_MINIMUM:
        return poids
    return round(poids * max(0.5, min(1.5, 1 + effet["moyenne"] / 10)))


@dataclass
class Opportunite:
    ticker: str
    points: dict[str, float] = field(default_factory=dict)
    catalyseurs: list[str] = field(default_factory=list)
    titres: list[Actu] = field(default_factory=list)  # actus porteuses d'un catalyseur
    signaux: list[str] = field(default_factory=list)
    nouveau: bool = False  # au moins une actu porteuse jamais vue
    dernier_cours: float | None = None
    lecture_ia: str = ""
    risques: str = ""

    @property
    def score(self) -> int:
        return int(max(0, min(100, round(sum(self.points.values())))))


def catalyseurs_de(actu: Actu) -> list[tuple[str, int]]:
    texte = f" {actu.titre} {actu.resume} ".lower()
    trouves = [(nom, poids) for nom, (poids, mots) in CATALYSEURS.items()
               if any(m in texte for m in mots)]
    if any(m in texte for m in NEGATIF[1]):
        trouves.append(("négatif", NEGATIF[0]))
    return trouves


def scorer(
    univers: list[str],
    historiques: dict[str, pd.DataFrame],
    actus: list[Actu],
    nouvelles: set[str],
    seuil_volume: float = 3.0,
    historique: dict[str, dict[str, float]] | None = None,
) -> list[Opportunite]:
    """Score chaque valeur de l'univers ; renvoie la liste triée, meilleur d'abord.
    ``nouvelles`` : liens d'actus jamais vus, pour savoir si le catalyseur est frais.
    ``historique`` : effet mesuré par catégorie de news (voir ``ajuster_poids``)."""
    historique = historique or {}
    resultats = []
    for ticker in univers:
        hist = historiques.get(ticker)
        if hist is None or len(hist) < 25:
            continue
        opp = Opportunite(ticker, dernier_cours=float(hist["Close"].dropna().iloc[-1]))

        positifs: dict[str, int] = {}
        negatif = 0
        for actu in (a for a in actus if a.ticker == ticker):
            trouves = catalyseurs_de(actu)
            if not trouves:
                continue
            opp.titres.append(actu)
            opp.nouveau |= actu.lien in nouvelles
            for nom, poids in trouves:
                if poids < 0:
                    negatif = poids
                else:
                    positifs[nom] = max(positifs.get(nom, 0), ajuster_poids(poids, historique.get(nom)))
        if positifs:
            # le meilleur catalyseur, plus un bonus si plusieurs types se cumulent
            opp.points["catalyseur"] = min(40, max(positifs.values()) + 5 * (len(positifs) - 1))
            opp.catalyseurs = sorted(positifs, key=positifs.get, reverse=True)
            effet = historique.get(opp.catalyseurs[0])
            if effet and effet["cas"] >= CAS_MINIMUM:
                opp.signaux.append(
                    f"par le passé, ce type de news a donné {effet['moyenne']:+.1f} pts sur 1 semaine "
                    f"face au marché ({effet['cas']} cas)"
                )
        if negatif:
            opp.points["actu négative"] = negatif
            opp.catalyseurs.append("négatif")

        v1m = ind.variation_sur_pct(hist, 21)
        sens = ind.tendance(hist)
        dynamique = max(0.0, min(v1m or 0.0, 25.0)) * 0.6
        if v1m is not None and v1m >= 5:
            opp.signaux.append(f"+{v1m:.0f} % sur 1 mois")
        if sens == "haussière":
            dynamique += 10
            opp.signaux.append("tendance haussière")
        elif sens == "baissière":
            opp.points["tendance baissière"] = -10
            opp.signaux.append("tendance baissière")
        opp.points["dynamique"] = min(25.0, dynamique)

        volume = ind.ratio_volume(hist)
        if volume is not None and volume >= 2:
            opp.points["volume"] = 20 if volume >= seuil_volume else 10
            opp.signaux.append(f"volume {volume:.1f}× la moyenne")

        seance = ind.variation_seance_pct(hist)
        if seance is not None and seance >= 3:
            opp.points["accélération"] = 15 if seance >= 6 else 8
            opp.signaux.append(f"+{seance:.1f} % aujourd'hui")

        niveau_rsi = ind.rsi(hist)
        if niveau_rsi is not None and niveau_rsi >= 75:
            opp.points["surachat"] = -10
            opp.signaux.append(f"RSI {niveau_rsi:.0f}, surachat")

        resultats.append(opp)
    resultats.sort(key=lambda o: o.score, reverse=True)
    return resultats


SCHEMA_IA = {
    "type": "object",
    "properties": {
        "evaluations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "score_catalyseur": {
                        "type": "integer",
                        "description": "Force du catalyseur pour une hausse, de -25 (très négatif) à 40.",
                    },
                    "lecture": {"type": "string", "description": "Pourquoi cela peut faire monter le cours, 2 phrases."},
                    "risques": {"type": "string", "description": "Ce qui peut mal tourner, 1 phrase."},
                },
                "required": ["ticker", "score_catalyseur", "lecture", "risques"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["evaluations"],
    "additionalProperties": False,
}

CONSIGNES_IA = """Tu évalues des catalyseurs boursiers pour un particulier français qui \
cherche, sur le thème de l'intelligence artificielle, des situations comme Nvidia ou Intel : \
une nouvelle concrète (partenariat, contrat de fabrication, prise de participation, \
résultats et prévisions relevés) qui peut entraîner une hausse durable du cours.

Pour chaque valeur, lis les titres fournis et note la force du catalyseur pour une hausse : \
40 pour un accord majeur qui change les perspectives de l'entreprise, 20 pour une nouvelle \
favorable mais ordinaire, 0 pour du bruit (articles de commentaire, listes « actions à \
acheter », rumeurs), négatif pour une mauvaise nouvelle. Juge l'impact sur cette valeur \
précisément, pas sur son partenaire. Sois exigeant : la plupart des titres valent 0 à 15. \
Si une section « Mémoire » est fournie, appuie-toi sur l'effet réellement mesuré des news \
passées du même type et du même secteur. Réponds en français."""


def affiner_avec_claude(
    opportunites: list[Opportunite], config_ia: dict[str, Any], modele: str | None, client: Any = None,
    memoire: str = "",
) -> bool:
    """Remplace la note « catalyseur » par mots-clés par celle de Claude.
    Renvoie False si Claude est indisponible (les notes par mots-clés restent)."""
    a_noter = [o for o in opportunites if o.titres]
    if not a_noter:
        return False
    lignes = []
    for o in a_noter:
        lignes.append(f"## {o.ticker} : {', '.join(o.signaux) or 'aucun signal technique'}")
        lignes += [f"- {a.titre} — {a.resume[:200]}" for a in o.titres[:6]]
    if memoire:
        lignes += ["", memoire]
    resultat = hyp.avec_claude("\n".join(lignes), config_ia, client,
                               schema=SCHEMA_IA, consignes=CONSIGNES_IA, modele=modele)
    if not resultat:
        return False
    par_ticker = {o.ticker: o for o in a_noter}
    for e in resultat.get("evaluations", []):
        o = par_ticker.get(e["ticker"])
        if o is None:
            continue
        o.points.pop("actu négative", None)
        o.points["catalyseur"] = max(-25, min(int(e["score_catalyseur"]), 40))
        o.lecture_ia = e["lecture"]
        o.risques = e["risques"]
    return True


def formater(o: Opportunite) -> str:
    lignes = [f"💎 {o.ticker} : score {o.score}/100"]
    if o.titres:
        a = o.titres[0]
        lignes.append(f"Catalyseur ({', '.join(o.catalyseurs)}) : {a.titre}")
    if o.lecture_ia:
        lignes.append(f"Lecture : {o.lecture_ia}")
    if o.signaux:
        lignes.append(f"Marché : {', '.join(o.signaux)}")
    if o.risques:
        lignes.append(f"Risques : {o.risques}")
    if o.dernier_cours is not None:
        lignes.append(f"Cours : {o.dernier_cours:.2f}")
    if o.titres:
        lignes.append(o.titres[0].lien)
    lignes.append("Avant d'acheter : lis l'article, décide ton prix de sortie, ne mise qu'une petite part.")
    return "\n".join(lignes)
