"""Point d'entrée : ``python -m boursicoteur <commande>``.

Commandes :
  veille  contrôle rapide (alertes de risque, actus nouvelles sur ton portefeuille)
  resume  résumé quotidien : contexte, pistes IA, introductions, hypothèses
  bilan   évalue les hypothèses arrivées à échéance et compare à la référence
  boucle  tourne en continu : veille toutes les N minutes, résumé et bilan aux heures prévues

L'outil ne passe aucun ordre : il informe, tu décides.
"""

from __future__ import annotations

import argparse
import logging
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

from . import config as cfg
from . import hypotheses as hyp
from . import signaux
from .journal import Journal
from .notifications import Notifieur
from .sources import actus as src_actus
from .sources import cours as src_cours
from .sources import introductions as src_intro

log = logging.getLogger("boursicoteur")
PARIS = ZoneInfo("Europe/Paris")
VIX = "^VIX"


def _dernier(historiques, ticker):
    hist = historiques.get(ticker)
    if hist is None or hist.empty:
        return None
    return float(hist["Close"].dropna().iloc[-1])


def veille(conf, journal: Journal, notifieur: Notifieur) -> None:
    portefeuille = [p["ticker"] for p in conf["portefeuille"]]
    devises = {p["devise"] for p in conf["portefeuille"] if p.get("devise", "EUR") != "EUR"}
    paires = {d: f"EUR{d}=X" for d in devises}
    historiques = src_cours.historiques(portefeuille + [VIX] + list(paires.values()), periode="6mo")
    taux = {d: _dernier(historiques, paire) for d, paire in paires.items()}

    alertes = signaux.alertes_portefeuille(
        conf["portefeuille"], historiques, conf["seuils"], {d: t for d, t in taux.items() if t}
    )
    alertes += signaux.alerte_marche(historiques.get(VIX), conf["seuils"]["vix_alerte"])
    for alerte in alertes:
        jour = date.min if alerte.unique else None
        if journal.alerte_inedite(alerte.cle, alerte.message, jour):
            notifieur.envoyer(alerte.message, titre="Alerte", urgent=alerte.urgente)

    # Actus nouvelles sur les valeurs que tu détiens
    actus = src_actus.collecter([], portefeuille)
    nouvelles = journal.nouvelles_actus([a.lien for a in actus])
    a_signaler = [a for a in actus if a.lien in nouvelles]
    if a_signaler:
        texte = "\n".join(f"• [{a.ticker}] {a.titre}\n  {a.lien}" for a in a_signaler[:10])
        notifieur.envoyer(texte, titre="Actus sur ton portefeuille")


def resume(conf, journal: Journal, notifieur: Notifieur) -> None:
    tickers = cfg.tickers_suivis(conf)
    historiques = src_cours.historiques(tickers + [VIX], periode="6mo")
    actus = src_actus.collecter(conf["flux_rss"], tickers)
    journal.nouvelles_actus([a.lien for a in actus])

    theme = conf["theme_ia"]
    pistes = signaux.scanner_theme_ia(
        theme["univers"], historiques, actus, theme["mots_cles"],
        conf["seuils"]["volume_inhabituel_x"], theme["pistes_max"],
    )

    intros = []
    if conf["introductions"]["nasdaq"]:
        intros += src_intro.calendrier_nasdaq()
    if conf["introductions"]["sec_s1"]:
        intros += src_intro.depots_sec(conf["introductions"]["sec_user_agent"])

    marche = {
        "VIX": _dernier(historiques, VIX),
        "reference": conf["reference"],
        "reference_cours": _dernier(historiques, conf["reference"]),
        "date": date.today().isoformat(),
    }
    donnees = hyp.preparer_donnees(actus, pistes, intros, marche)
    resultat = hyp.avec_claude(donnees, conf["ia"])

    if resultat:
        hypotheses = hyp.depuis_claude(resultat, set(historiques))
        contexte = resultat.get("contexte", "")
        intros_a_suivre = resultat.get("introductions_a_suivre", [])
    else:
        hypotheses = hyp.par_regles(pistes)
        contexte = "(Résumé IA indisponible : hypothèses par règles simples.)"
        intros_a_suivre = []

    reference = _dernier(historiques, conf["reference"])
    for h in hypotheses:
        journal.noter(h, _dernier(historiques, h.ticker), reference)

    notifieur.envoyer(
        formater_resume(contexte, pistes, hypotheses, intros_a_suivre),
        titre=f"Résumé du {date.today():%d/%m/%Y}",
    )


def formater_resume(contexte, pistes, hypotheses, intros_a_suivre) -> str:
    parties = ["🌍 Contexte", contexte or "—", ""]
    parties.append("🤖 Pistes IA du jour")
    if pistes:
        for p in pistes:
            parties.append(f"• {p.ticker} (score {p.score}) : {', '.join(p.raisons) or 'pas de signal fort'}")
    else:
        parties.append("• Aucune donnée de cours disponible.")
    parties.append("")
    parties.append("🧭 Hypothèses (notées dans le journal)")
    if hypotheses:
        for h in hypotheses:
            fleche = "⬆️" if h.sens == "hausse" else "⬇️"
            parties.append(
                f"{fleche} {h.ticker}, {h.horizon_jours} j, confiance {h.confiance}/5\n"
                f"   Pourquoi : {h.raisonnement}\n   Invalidée si : {h.invalidation}"
            )
    else:
        parties.append("• Aucune hypothèse assez solide aujourd'hui. C'est une réponse valable.")
    parties.append("")
    # Seules les introductions retenues par l'IA sont montrées ; sans
    # sélection, la rubrique disparaît plutôt que d'afficher une liste brute.
    if intros_a_suivre:
        parties.append("🚀 Introductions en bourse à suivre")
        for i in intros_a_suivre:
            parties.append(f"• {i['societe']} : {i['pourquoi']}\n   Risques : {i['risques']}")
        parties.append("")
    parties.append("Rappel : ce sont des hypothèses à vérifier, pas des ordres.")
    return "\n".join(parties)


def bilan(conf, journal: Journal, notifieur: Notifieur) -> None:
    a_evaluer = journal.a_evaluer()
    if a_evaluer:
        tickers = list({l["ticker"] for l in a_evaluer} | {conf["reference"]})
        historiques = src_cours.historiques(tickers, periode="5d")
        reference = _dernier(historiques, conf["reference"])
        for ligne in a_evaluer:
            cours = _dernier(historiques, ligne["ticker"])
            if cours is not None:
                journal.evaluer(ligne["id"], cours, reference)

    stats = journal.bilan()
    if not stats["evaluees"]:
        texte = "Aucune hypothèse arrivée à échéance pour l'instant."
    else:
        ref = stats["reference_moyenne_pct"]
        texte = (
            f"Hypothèses évaluées : {stats['evaluees']}\n"
            f"Taux de réussite : {stats['taux_reussite_pct']:.0f} %\n"
            f"Gain moyen en les suivant (hors frais) : {stats['gain_moyen_pct']:+.2f} %\n"
            f"Référence {conf['reference']} sur les mêmes périodes : "
            + (f"{ref:+.2f} %" if ref is not None else "n.d.")
            + "\n\nL'outil n'est utile que si le gain moyen dépasse nettement la référence, "
            "frais compris, sur plusieurs mois."
        )
    notifieur.envoyer(texte, titre="Bilan des hypothèses")


def boucle(conf, journal: Journal, notifieur: Notifieur) -> None:
    plan = conf["planification"]
    intervalle = int(plan["intervalle_veille_min"]) * 60
    heure, minute = (int(x) for x in str(plan["heure_resume"]).split(":"))
    dernier_resume = dernier_bilan = None
    log.info("Boucle démarrée : veille toutes les %s min.", plan["intervalle_veille_min"])
    while True:
        maintenant = datetime.now(PARIS)
        _proteger("veille", veille, conf, journal, notifieur)
        if (maintenant.hour, maintenant.minute) >= (heure, minute) and dernier_resume != maintenant.date():
            _proteger("resume", resume, conf, journal, notifieur)
            dernier_resume = maintenant.date()
            if maintenant.day == int(plan["jour_bilan"]) and dernier_bilan != maintenant.date():
                _proteger("bilan", bilan, conf, journal, notifieur)
                dernier_bilan = maintenant.date()
        time.sleep(intervalle)


def _proteger(nom, action, *args) -> None:
    """Une erreur ponctuelle (réseau, source en panne) ne doit pas arrêter la boucle."""
    try:
        action(*args)
    except Exception:
        log.exception("Échec de « %s », nouvel essai au prochain passage.", nom)


COMMANDES = {"veille": veille, "resume": resume, "bilan": bilan, "boucle": boucle}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="boursicoteur", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("commande", choices=COMMANDES)
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    conf = cfg.charger(args.config)
    journal = Journal(conf["base_de_donnees"])
    COMMANDES[args.commande](conf, journal, Notifieur(conf["notifications"]))
