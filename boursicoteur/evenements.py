"""Actus non boursières : ce qui se passe dans le monde, et qui en profitera en bourse.

Un site boursier qui conseille d'acheter arrive après le marché. Ici, on lit
des sources de première main (actualité mondiale, autorisations de
médicaments, contrats publics de défense, énergie) et Claude relie chaque
événement aux valeurs suivies, en privilégiant les effets de second rang :
un fournisseur, un client, un concurrent, le coût d'une matière.

Chaque lien proposé est noté deux fois au journal : comme catalyseur (effet
mesuré à 1 jour, 1 semaine, 4 semaines face à l'ETF de référence) et comme
hypothèse (évaluée à son échéance). Le bilan mensuel dira si ces pistes font
mieux que les actus boursières classiques.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .conseils import enveloppe_de
from .sources.actus import Actu

SCHEMA = {
    "type": "object",
    "properties": {
        "effets": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "evenement": {"type": "integer", "description": "Numéro de l'événement dans la liste."},
                    "ticker": {"type": "string"},
                    "sens": {"type": "string", "enum": ["hausse", "baisse"]},
                    "rang": {"type": "string", "enum": ["direct", "indirect"]},
                    "chaine": {"type": "string",
                               "description": "Chaîne de cause à effet, 1 à 2 phrases (A, donc B, donc C)."},
                    "delai_jours": {"type": "integer",
                                    "description": "Jours avant que l'effet se voie dans le cours ou les résultats."},
                    "confiance": {"type": "integer", "enum": [1, 2, 3, 4, 5]},
                    "invalidation": {"type": "string", "description": "Ce qui montrerait que le lien est faux."},
                },
                "required": ["evenement", "ticker", "sens", "rang", "chaine", "delai_jours", "confiance",
                             "invalidation"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["effets"],
    "additionalProperties": False,
}

CONSIGNES = """Tu aides un particulier français (PEA et compte-titres) à anticiper l'effet \
en bourse d'actualités qui ne sont pas des actualités boursières : géopolitique, santé \
publique, autorisations de médicaments, contrats publics, énergie, climat, réglementation.

Pour chaque événement numéroté, cherche les valeurs de la liste fournie (et seulement \
elles) qui devraient en profiter ou en souffrir :
- « direct » : l'entreprise est citée ou directement concernée ;
- « indirect » : l'effet passe par un intermédiaire (fournisseur, client, concurrent, \
coût d'une matière, d'un transport ou de l'énergie, changement de demande).

Les effets directs et évidents sont souvent dans le cours en quelques minutes : l'intérêt \
est dans les effets de second rang, moins visibles, qui mettent des jours ou des semaines \
à se voir. Explique la chaîne de cause à effet en une ou deux phrases, donne un délai \
réaliste et ce qui invaliderait le lien.

Sois très sélectif : la plupart des actualités n'ont aucun effet exploitable, et une liste \
vide est une bonne réponse. Ne force jamais un lien. Une confiance de 4 ou 5 doit rester \
rare. Si une section « Mémoire » est fournie, elle donne l'effet réellement mesuré des \
pistes passées : baisse ta confiance pour les types de liens qui n'ont pas marché. \
Réponds en français."""


@dataclass
class Effet:
    actu: Actu
    ticker: str
    sens: str
    rang: str
    chaine: str
    delai_jours: int
    confiance: int
    invalidation: str
    theme: str = ""

    @property
    def categorie(self) -> str:
        """Catégorie notée au journal : le bilan mesure chaque type de lien à part."""
        return f"événement {self.rang}, {self.sens} attendue"


def preparer(actus: list[Actu], valeurs: dict[str, str], memoire: str = "") -> str:
    """Texte envoyé à Claude : les événements numérotés puis les valeurs suivies."""
    lignes = ["## Événements"]
    for i, a in enumerate(actus):
        lignes.append(f"{i}. {a.titre} ({a.source}) {a.resume[:300]}")
    lignes.append("\n## Valeurs suivies (ticker : thème)")
    lignes += [f"- {t} : {theme}" for t, theme in valeurs.items()]
    if memoire:
        lignes += ["", memoire]
    return "\n".join(lignes)


def lire_reponse(resultat: dict[str, Any] | None, actus: list[Actu], valeurs: dict[str, str]) -> list[Effet]:
    """Garde les effets qui citent un événement et une valeur connus."""
    effets = []
    for e in (resultat or {}).get("effets", []):
        if not 0 <= e["evenement"] < len(actus) or e["ticker"] not in valeurs:
            continue
        effets.append(Effet(actus[e["evenement"]], e["ticker"], e["sens"], e["rang"], e["chaine"],
                            max(5, min(int(e["delai_jours"]), 90)), int(e["confiance"]), e["invalidation"],
                            valeurs[e["ticker"]]))
    effets.sort(key=lambda e: e.confiance, reverse=True)
    return effets


def formater(e: Effet) -> str:
    fleche = "⬆️ hausse" if e.sens == "hausse" else "⬇️ baisse"
    etiquettes = " · ".join(filter(None, (e.theme, enveloppe_de(e.ticker))))
    return "\n".join([
        f"🔗 {e.ticker} ({etiquettes}) : {fleche} attendue, effet {e.rang}, confiance {e.confiance}/5",
        f"Événement : {e.actu.titre} ({e.actu.source})",
        f"Chaîne : {e.chaine}",
        f"Délai : environ {e.delai_jours} jours",
        f"Invalidée si : {e.invalidation}",
        e.actu.lien,
        "Piste à vérifier, pas un ordre : regarde si le cours a déjà bougé avant d'agir.",
    ])
