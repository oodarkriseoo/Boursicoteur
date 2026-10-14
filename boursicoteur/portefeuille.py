"""Suivi du portefeuille : valeur, performance et poids de chaque ligne et de
chaque enveloppe (PEA, compte-titres…), en euros. Une photo quotidienne de la
valeur permet de comparer le portefeuille à l'ETF de référence dans le temps.

Les cours viennent des historiques déjà récupérés ; une valeur cotée dans une
autre devise est convertie au taux du jour. Une ligne sans cours (titre non
coté, source en panne) est comptée à son prix de revient et signalée, pour que
le total reste juste à peu près plutôt que faux de toute une ligne.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import pandas as pd

POIDS_LOURD_PCT = 25.0  # au-delà, une seule ligne pèse trop dans le total


@dataclass
class Ligne:
    ticker: str
    nom: str
    enveloppe: str
    quantite: float
    valeur_eur: float
    cout_eur: float | None  # None si le prix de revient n'est pas renseigné
    variation_jour_eur: float = 0.0
    cotee: bool = True  # False : valorisée au prix de revient faute de cours
    poids_pct: float = 0.0  # dans le total
    poids_enveloppe_pct: float = 0.0

    @property
    def gain_eur(self) -> float | None:
        """Sans cours, la plus-value est inconnue (pas nulle)."""
        return None if self.cout_eur is None or not self.cotee else self.valeur_eur - self.cout_eur

    @property
    def gain_pct(self) -> float | None:
        return None if self.gain_eur is None or not self.cout_eur else 100 * self.gain_eur / self.cout_eur


@dataclass
class Enveloppe:
    nom: str
    lignes: list[Ligne] = field(default_factory=list)
    poids_pct: float = 0.0

    @property
    def valeur_eur(self) -> float:
        return sum(l.valeur_eur for l in self.lignes)

    @property
    def variation_jour_eur(self) -> float:
        return sum(l.variation_jour_eur for l in self.lignes)

    @property
    def cout_eur(self) -> float:
        """Coût des seules lignes dont la plus-value est connue."""
        return sum(l.cout_eur for l in self.lignes if l.gain_eur is not None)

    @property
    def gain_eur(self) -> float:
        return sum(l.gain_eur for l in self.lignes if l.gain_eur is not None)

    @property
    def gain_pct(self) -> float | None:
        return 100 * self.gain_eur / self.cout_eur if self.cout_eur else None


@dataclass
class Etat:
    enveloppes: list[Enveloppe]

    @property
    def lignes(self) -> list[Ligne]:
        return [l for e in self.enveloppes for l in e.lignes]

    @property
    def valeur_eur(self) -> float:
        return sum(e.valeur_eur for e in self.enveloppes)

    @property
    def variation_jour_eur(self) -> float:
        return sum(e.variation_jour_eur for e in self.enveloppes)

    @property
    def gain_eur(self) -> float:
        return sum(e.gain_eur for e in self.enveloppes)

    @property
    def gain_pct(self) -> float | None:
        cout = sum(e.cout_eur for e in self.enveloppes)
        return 100 * self.gain_eur / cout if cout else None


def _deux_derniers(hist: pd.DataFrame | None) -> tuple[float | None, float | None]:
    if hist is None or hist.empty:
        return None, None
    cloture = hist["Close"].dropna()
    if cloture.empty:
        return None, None
    veille = float(cloture.iloc[-2]) if len(cloture) > 1 else None
    return float(cloture.iloc[-1]), veille


def valoriser(positions: list[dict[str, Any]], historiques: dict[str, pd.DataFrame],
              taux: dict[str, float | None]) -> Etat:
    """``taux`` : devise → nombre d'unités de cette devise pour 1 € (EURUSD=X)."""
    par_enveloppe: dict[str, Enveloppe] = {}
    for p in positions:
        quantite = float(p.get("quantite") or 0)
        if quantite <= 0:
            continue
        devise = p.get("devise", "EUR")
        change = 1.0 if devise == "EUR" else taux.get(devise)
        cours, veille = _deux_derniers(historiques.get(p["ticker"]))
        prix_revient = p.get("prix_revient")
        cout = quantite * prix_revient if prix_revient else None
        if cours is not None and change:
            valeur = quantite * cours / change
            variation = quantite * (cours - veille) / change if veille is not None else 0.0
            cotee = True
        elif cout is not None:
            valeur, variation, cotee = cout, 0.0, False
        else:
            continue  # ni cours ni prix de revient : rien de chiffrable
        nom_env = str(p.get("enveloppe") or "Autre")
        ligne = Ligne(p["ticker"], p.get("nom", p["ticker"]), nom_env, quantite, valeur, cout,
                      variation, cotee)
        par_enveloppe.setdefault(nom_env, Enveloppe(nom_env)).lignes.append(ligne)

    etat = Etat(sorted(par_enveloppe.values(), key=lambda e: -e.valeur_eur))
    total = etat.valeur_eur
    for env in etat.enveloppes:
        env.lignes.sort(key=lambda l: -l.valeur_eur)
        env.poids_pct = 100 * env.valeur_eur / total if total else 0.0
        for l in env.lignes:
            l.poids_pct = 100 * l.valeur_eur / total if total else 0.0
            l.poids_enveloppe_pct = 100 * l.valeur_eur / env.valeur_eur if env.valeur_eur else 0.0
    return etat


def _euros(x: float) -> str:
    return f"{x:,.0f} €".replace(",", " ")


def _gain(eur: float | None, pct: float | None) -> str:
    if eur is None:
        return "prix de revient inconnu"
    return f"{'+' if eur >= 0 else '−'}{_euros(abs(eur))}" + (f" ({pct:+.1f} %)" if pct is not None else "")


def synthese(etat: Etat) -> list[str]:
    """Quelques lignes pour le résumé du matin : total et chaque enveloppe."""
    if not etat.enveloppes:
        return []
    lignes = [f"💼 {_euros(etat.valeur_eur)}, plus-value {_gain(etat.gain_eur, etat.gain_pct)}, "
              f"séance {_gain(etat.variation_jour_eur, None)}"]
    sans_cours = [l.nom for l in etat.lignes if not l.cotee]
    if sans_cours:
        lignes[0] += f" (sans cours, comptées au prix de revient : {', '.join(sans_cours)})"
    for env in etat.enveloppes:
        lignes.append(f"• {env.nom} : {_euros(env.valeur_eur)} ({env.poids_pct:.0f} %), "
                      f"plus-value {_gain(env.gain_eur, env.gain_pct)}")
    return lignes


def formater(etat: Etat) -> str:
    """Le détail ligne par ligne, enveloppe par enveloppe."""
    if not etat.enveloppes:
        return "Aucune position chiffrable : renseigne quantite et prix_revient dans config.yaml."
    parties = synthese(etat)
    for env in etat.enveloppes:
        parties += ["", f"{env.nom} · {_euros(env.valeur_eur)} · séance {_gain(env.variation_jour_eur, None)}"]
        for l in env.lignes:
            detail = (f"plus-value {_gain(l.gain_eur, l.gain_pct)}" if l.cotee
                      else "sans cours : comptée au prix de revient")
            parties.append(f"• {l.nom} ({l.ticker}) : {_euros(l.valeur_eur)}, "
                           f"{l.poids_enveloppe_pct:.0f} % de l'enveloppe, {l.poids_pct:.0f} % du total\n"
                           f"   {detail}")
    lourdes = [l for l in etat.lignes if l.poids_pct > POIDS_LOURD_PCT]
    if lourdes and len(etat.lignes) > 1:
        parties += ["", "⚖️ " + ", ".join(f"{l.nom} pèse {l.poids_pct:.0f} %" for l in lourdes)
                    + f" du total (plus de {POIDS_LOURD_PCT:.0f} %) : une mauvaise nouvelle sur cette "
                      "ligne toucherait tout le portefeuille."]
    return "\n".join(parties)


# --- historique : le portefeuille face à la référence ---------------------------

PERIODES = [("1 semaine", 7), ("1 mois", 30), ("3 mois", 91), ("1 an", 365)]
BARRES = "▁▂▃▄▅▆▇█"


def photo(etat: Etat, precedente: dict | None) -> tuple[float, dict[str, list]] | None:
    """(apport en €, lignes) pour la photo du jour, ou None s'il vaut mieux ne
    pas photographier : une ligne cotée la veille et sans cours aujourd'hui
    ferait croire à un mouvement qui n'a pas eu lieu.

    L'apport est l'argent ajouté ou retiré depuis la photo précédente (achat,
    vente, nouvelle ligne), compté au cours du jour : il est retiré de la
    performance pour qu'un versement ne passe pas pour un gain."""
    lignes = {l.ticker: [l.quantite, l.valeur_eur / l.quantite, l.cotee] for l in etat.lignes}
    if precedente is None:
        return 0.0, lignes
    avant = precedente["lignes"]
    if any(a[2] and t in lignes and not lignes[t][2] for t, a in avant.items()):
        return None
    apport = sum((q - avant.get(t, [0])[0]) * prix for t, (q, prix, _) in lignes.items())
    # ligne disparue (vendue en entier) : comptée à son dernier prix connu
    apport -= sum(q * prix for t, (q, prix, _) in avant.items() if t not in lignes)
    return apport, lignes


def indices(photos) -> list[tuple[str, float, float]]:
    """(jour, indice du portefeuille, indice de la référence), base 1 au
    premier jour. L'indice du portefeuille enchaîne les rendements de chaque
    jour, apports retirés (rendement pondéré par le temps)."""
    resultat: list[tuple[str, float, float]] = []
    indice = 1.0
    for i, p in enumerate(photos):
        if i and photos[i - 1]["valeur_eur"] > 0:
            indice *= (p["valeur_eur"] - p["apport_eur"]) / photos[i - 1]["valeur_eur"]
        resultat.append((p["jour"], indice, p["reference"] / photos[0]["reference"]))
    return resultat


def _pct(x: float) -> str:
    return f"{100 * x:+.1f} %"


def courbe(valeurs: list[float], bas: float, haut: float) -> str:
    etendue = haut - bas or 1.0
    return "".join(BARRES[min(len(BARRES) - 1, int((v - bas) / etendue * len(BARRES)))] for v in valeurs)


def formater_performance(photos, reference: str, aujourd_hui: date | None = None, points: int = 30) -> str:
    """Performance du portefeuille et de la référence sur plusieurs durées,
    avec les deux courbes des derniers jours sur la même échelle."""
    if not photos:
        return ""
    debut = date.fromisoformat(photos[0]["jour"])
    if len(photos) < 2:
        return (f"📈 Historique commencé le {debut:%d/%m/%Y} : la comparaison avec {reference} "
                "apparaît dès demain.")
    aujourd_hui = aujourd_hui or date.today()
    serie = indices(photos)
    _, fin_p, fin_r = serie[-1]
    lignes = [f"📈 Face à {reference} (historique depuis le {debut:%d/%m/%Y})"]
    periodes = [(nom, aujourd_hui - timedelta(days=jours)) for nom, jours in PERIODES]
    for nom, depuis in periodes:
        bases = [x for x in serie if date.fromisoformat(x[0]) <= depuis]
        if bases:
            _, p0, r0 = bases[-1]
            lignes.append(f"• {nom} : toi {_pct(fin_p / p0 - 1)}, référence {_pct(fin_r / r0 - 1)}, "
                          f"écart {100 * (fin_p / p0 - fin_r / r0):+.1f} pts")
    lignes.append(f"• Depuis le début : toi {_pct(fin_p - 1)}, référence {_pct(fin_r - 1)}, "
                  f"écart {100 * (fin_p - fin_r):+.1f} pts")
    recents = serie[-points:]
    tous = [x[1] for x in recents] + [x[2] for x in recents]
    lignes.append(f"Toi        {courbe([x[1] for x in recents], min(tous), max(tous))}")
    lignes.append(f"Référence  {courbe([x[2] for x in recents], min(tous), max(tous))}")
    lignes.append(f"({len(recents)} derniers jours, même échelle ; versements et ventes retirés du calcul)")
    return "\n".join(lignes)
