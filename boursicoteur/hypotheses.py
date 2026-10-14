"""Formulation des hypothèses haussières / baissières.

Avec une clé d'API Anthropic, Claude lit les actus du jour, les indicateurs
et les introductions en bourse, puis propose des hypothèses structurées.
Sans clé, des règles simples prennent le relais à partir des pistes par thème.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from .journal import Hypothese
from .signaux import Piste
from .sources.actus import Actu
from .sources.introductions import Introduction

log = logging.getLogger(__name__)

SCHEMA = {
    "type": "object",
    "properties": {
        "contexte": {
            "type": "string",
            "description": "Résumé en français du contexte de marché et économique du jour, 5 phrases maximum.",
        },
        "hypotheses": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ticker": {"type": "string"},
                    "sens": {"type": "string", "enum": ["hausse", "baisse"]},
                    "horizon_jours": {"type": "integer"},
                    "confiance": {"type": "integer", "enum": [1, 2, 3, 4, 5]},
                    "raisonnement": {"type": "string"},
                    "invalidation": {"type": "string"},
                },
                "required": ["ticker", "sens", "horizon_jours", "confiance", "raisonnement", "invalidation"],
                "additionalProperties": False,
            },
        },
        "introductions_a_suivre": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "societe": {"type": "string"},
                    "pourquoi": {"type": "string"},
                    "risques": {"type": "string"},
                },
                "required": ["societe", "pourquoi", "risques"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["contexte", "hypotheses", "introductions_a_suivre"],
    "additionalProperties": False,
}

CONSIGNES = """Tu aides un particulier français à suivre les marchés. Il investit via un PEA \
et un compte-titres. Il suit plusieurs thèmes (intelligence artificielle, santé, \
électrification, matériaux, défense, banques…), y compris des petites valeurs et des \
introductions en bourse peu connues. Seules les actions européennes vont dans le PEA.

À partir des données fournies (et seulement d'elles), rédige en français :
1. un résumé du contexte de marché et économique ;
2. au plus 5 hypothèses haussières ou baissières sur des tickers présents dans les données, \
chacune avec un horizon en jours (entre 5 et 90), une confiance de 1 à 5, un raisonnement \
court et ce qui invaliderait l'hypothèse ;
3. au plus 3 introductions en bourse ou dépôts de dossier liés à l'IA qui méritent d'être \
suivis, avec leurs risques.

Pour les hypothèses haussières, cherche en priorité des catalyseurs concrets dans les actus : partenariat, contrat de fabrication ou d'approvisionnement, prise de participation, résultats ou prévisions relevés, comme l'annonce d'Intel fabriquant des puces pour Nvidia. Un catalyseur confirmé par une hausse du cours et des volumes vaut plus qu'un simple mouvement technique.

La section « Mémoire » donne l'effet réellement mesuré des news passées par secteur, \
le bilan de tes hypothèses précédentes et ce qu'ont fait les cours après les dernières \
news de chaque valeur : appuie-toi dessus, et baisse ta confiance si ton bilan est faible.

Sois sobre et honnête : une confiance de 4 ou 5 doit rester rare, et il vaut mieux ne \
formuler aucune hypothèse que d'en inventer une sans appui dans les données. Rappelle-toi \
que la plupart des valeurs qui montent très vite finissent par corriger fortement. \
Ces hypothèses servent à un journal évalué dans le temps, pas à des ordres automatiques."""


def preparer_donnees(
    actus: list[Actu],
    pistes: list[Piste],
    introductions: list[Introduction],
    marche: dict[str, Any],
) -> str:
    lignes = ["## Marché", json.dumps(marche, ensure_ascii=False, default=str)]
    lignes.append("\n## Pistes par thème (signaux techniques)")
    for p in pistes:
        cours = f"{p.dernier_cours:.2f}" if p.dernier_cours is not None else "?"
        lignes.append(f"- {p.ticker} : score {p.score}, cours {cours}, {', '.join(p.raisons) or 'aucun signal'}")
    lignes.append("\n## Introductions en bourse et dépôts récents")
    for i in introductions[:30]:
        details = " ".join(x for x in (i.ticker, i.marche, i.prix, i.date, i.montant) if x)
        lignes.append(f"- {i.societe} ({i.statut}) {details}")
    lignes.append("\n## Actus récentes")
    for a in actus[:80]:
        prefixe = f"[{a.ticker}] " if a.ticker else ""
        lignes.append(f"- {prefixe}{a.titre} ({a.source}) {a.resume[:200]}")
    return "\n".join(lignes)


def entetes(config_ia: dict[str, Any]) -> dict[str, str]:
    """Une clé d'API non rattachée à un espace de travail exige l'en-tête
    ``anthropic-workspace-id`` ; son identifiant se lit dans l'environnement."""
    espace = os.environ.get(config_ia.get("espace_env", "ANTHROPIC_WORKSPACE_ID"), "").strip()
    return {"anthropic-workspace-id": espace} if espace else {}


def avec_claude(
    donnees: str,
    config_ia: dict[str, Any],
    client: Any = None,
    schema: dict[str, Any] = SCHEMA,
    consignes: str = CONSIGNES,
    modele: str | None = None,
) -> dict[str, Any] | None:
    """Renvoie le JSON produit par Claude, ou None si indisponible.

    ``schema``, ``consignes`` et ``modele`` permettent de réutiliser l'appel
    pour d'autres tâches (notation des catalyseurs par exemple)."""
    if not config_ia.get("activee", True):
        return None
    if client is None:
        if not os.environ.get(config_ia.get("cle_env", "ANTHROPIC_API_KEY")):
            log.info("Pas de clé d'API Anthropic : hypothèses par règles simples.")
            return None
        import anthropic

        client = anthropic.Anthropic(api_key=os.environ[config_ia.get("cle_env", "ANTHROPIC_API_KEY")],
                                     default_headers=entetes(config_ia))

    try:
        reponse = client.beta.messages.create(
            model=modele or config_ia.get("modele", "claude-opus-5-5"),
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=consignes,
            output_config={
                "effort": config_ia.get("effort", "low"),
                "format": {"type": "json_schema", "schema": schema},
            },
            messages=[{"role": "user", "content": donnees}],
        )
    except Exception as exc:
        log.warning("Appel à Claude impossible : %s", exc)
        return None

    if reponse.stop_reason == "refusal":
        log.warning("Claude a décliné la demande : %s", reponse.stop_details)
        return None
    if reponse.stop_reason == "max_tokens":
        log.warning("Réponse de Claude tronquée (max_tokens).")
        return None
    texte = next((b.text for b in reponse.content if b.type == "text"), None)
    if not texte:
        return None
    try:
        return json.loads(texte)
    except json.JSONDecodeError:
        log.warning("Réponse de Claude illisible.")
        return None


def depuis_claude(resultat: dict[str, Any], tickers_connus: set[str]) -> list[Hypothese]:
    hypotheses = []
    for h in resultat.get("hypotheses", []):
        if h["ticker"] not in tickers_connus:
            continue  # on n'évalue que ce qu'on sait suivre
        hypotheses.append(
            Hypothese(
                ticker=h["ticker"],
                sens=h["sens"],
                horizon_jours=max(5, min(int(h["horizon_jours"]), 90)),
                confiance=max(1, min(int(h["confiance"]), 5)),
                raisonnement=h["raisonnement"],
                invalidation=h["invalidation"],
                origine="ia",
            )
        )
    return hypotheses


def par_regles(pistes: list[Piste]) -> list[Hypothese]:
    """Repli sans IA : une hypothèse de hausse à 20 jours sur les pistes
    les mieux notées qui sont en tendance haussière."""
    hypotheses = []
    for p in pistes:
        if p.tendance != "haussière" or p.score < 2:
            continue
        hypotheses.append(
            Hypothese(
                ticker=p.ticker,
                sens="hausse",
                horizon_jours=20,
                confiance=2,
                raisonnement="Signaux techniques : " + ", ".join(p.raisons),
                invalidation="Clôture sous la moyenne mobile 50 jours.",
                origine="regles",
            )
        )
    return hypotheses
