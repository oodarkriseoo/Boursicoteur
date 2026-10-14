"""Fiche mémoire envoyée à Claude : ce que le journal a appris, en chiffres.

Plutôt qu'un long texte libre par secteur, Claude reçoit chaque jour une
fiche courte recalculée depuis le journal :

1. l'effet mesuré de chaque type de news, par secteur ;
2. le bilan de ses propres hypothèses, pour ajuster sa confiance ;
3. pour chaque valeur, les dernières news marquantes et ce que le cours a fait ensuite.

Des chiffres tirés des données réelles, pas des opinions : c'est ce qui
permet à l'IA de raisonner « avec un historique ».
"""

from __future__ import annotations

from typing import Any

from .journal import Journal

SECTEURS = {
    **dict.fromkeys(["NVDA", "AMD", "AVGO", "TSM", "ASML.AS", "ARM", "MU", "INTC", "MRVL", "AMAT",
                     "LRCX", "STMPA.PA", "SOI.PA", "QCOM"], "semi-conducteurs"),
    **dict.fromkeys(["ANET", "ALAB", "CRDO", "SMCI", "CLS"], "réseau et serveurs"),
    **dict.fromkeys(["VRT", "VST", "CEG"], "énergie des data centers"),
    **dict.fromkeys(["CRWV", "NBIS", "APLD", "IREN"], "calcul IA en location"),
    **dict.fromkeys(["PLTR", "MSFT", "ORCL", "GOOGL", "AMZN", "META"], "logiciel et cloud"),
    **dict.fromkeys(["MC.PA", "RMS.PA", "KER.PA"], "luxe"),
    **dict.fromkeys(["SAN.PA", "NOV.DE", "NOVO-B.CO"], "santé"),
    **dict.fromkeys(["STLAP.PA", "RNO.PA"], "automobile"),
    "SU.PA": "équipements électriques",
    "BTC-EUR": "crypto",
}


def secteur(ticker: str, surcharges: dict[str, str] | None = None) -> str:
    return (surcharges or {}).get(ticker) or SECTEURS.get(ticker, "autre")


def effet_par_secteur(journal: Journal, surcharges: dict[str, str] | None = None,
                      colonne: str = "ecart_1s") -> dict[tuple[str, str], dict[str, float]]:
    """(secteur, catégorie de news) → nombre de cas et écart moyen à la référence."""
    effets: dict[tuple[str, str], list[float]] = {}
    for l in journal.db.execute(
        f"SELECT ticker, categories, {colonne} AS e FROM catalyseurs WHERE {colonne} IS NOT NULL"
    ):
        for categorie in filter(None, l["categories"].split(",")):
            effets.setdefault((secteur(l["ticker"], surcharges), categorie), []).append(l["e"])
    return {k: {"cas": len(v), "moyenne": sum(v) / len(v)} for k, v in effets.items()}


def fiche(journal: Journal, tickers: list[str] | None = None,
          surcharges: dict[str, str] | None = None, max_lignes: int = 60) -> str:
    """Fiche texte, bornée en longueur pour garder l'appel à Claude économique."""
    lignes = ["## Mémoire (calculée depuis le journal, chiffres réels)"]

    effets = effet_par_secteur(journal, surcharges)
    lignes.append("### Effet des news sur 1 semaine, en points d'écart à l'ETF de référence")
    if effets:
        for (sect, cat), e in sorted(effets.items(), key=lambda x: -x[1]["cas"])[:20]:
            lignes.append(f"- {sect}, {cat} : {e['moyenne']:+.1f} pts ({e['cas']} cas)")
    else:
        lignes.append("- Pas encore de mesure : l'historique démarre.")

    stats = journal.bilan()
    lignes.append("### Bilan des hypothèses passées")
    if stats["evaluees"]:
        lignes.append(
            f"- {stats['evaluees']} évaluées, {stats['taux_reussite_pct']:.0f} % réussies, "
            f"gain moyen {stats['gain_moyen_pct']:+.1f} % contre {stats['reference_moyenne_pct'] or 0:+.1f} % "
            "pour la référence. Ajuste ta confiance en conséquence."
        )
    else:
        lignes.append("- Aucune hypothèse encore évaluée.")

    lignes.append("### Dernières news marquantes par valeur et suite du cours")
    filtre = ""
    params: list[Any] = []
    if tickers:
        filtre = f"WHERE ticker IN ({','.join('?' * len(tickers))})"
        params = list(tickers)
    vus: dict[str, int] = {}
    for l in journal.db.execute(f"SELECT * FROM catalyseurs {filtre} ORDER BY vu_le DESC", params):
        if vus.get(l["ticker"], 0) >= 3:
            continue
        vus[l["ticker"]] = vus.get(l["ticker"], 0) + 1
        suite = ", ".join(
            f"{nom} {l[col]:+.1f} pts" for nom, col in (("1 j", "ecart_1j"), ("1 sem.", "ecart_1s"),
                                                      ("4 sem.", "ecart_4s")) if l[col] is not None
        ) or "pas encore mesuré"
        lignes.append(f"- [{l['ticker']}] {l['vu_le'][:10]} {l['titre'][:120]} ({l['categories']}) : {suite}")
    if not vus:
        lignes.append("- Aucune encore.")
    return "\n".join(lignes[:max_lignes])
