"""Point d'entrée : ``python -m boursicoteur <commande>``.

Commandes :
  veille  contrôle rapide (alertes de chute, actus nouvelles sur ton portefeuille)
  opportunites  repère les valeurs IA portées par un catalyseur frais, score sur 100
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
from . import conseils as cons
from . import hypotheses as hyp
from . import opportunites as opp
from . import signaux
from .journal import Hypothese, Journal
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
    suivis = [c["ticker"] for c in journal.conseils_ouverts()]
    historiques = src_cours.historiques(
        list(dict.fromkeys(portefeuille + suivis)) + [VIX] + list(paires.values()), periode="6mo")
    taux = {d: _dernier(historiques, paire) for d, paire in paires.items()}

    # Actus d'abord : la dernière sur chaque valeur accompagne les alertes de chute
    actus = src_actus.collecter([], portefeuille)
    titres = {}
    for a in actus:
        titres.setdefault(a.ticker, a)  # les flux listent le plus récent en premier
    cours = {t: c for t in portefeuille if (c := _dernier(historiques, t)) is not None}
    precedents = journal.echanger_cours(cours)

    alertes = signaux.alertes_portefeuille(
        conf["portefeuille"], historiques, conf["seuils"], {d: t for d, t in taux.items() if t},
        precedents, titres,
    )
    alertes += signaux.alerte_marche(historiques.get(VIX), conf["seuils"]["vix_alerte"])
    for alerte in alertes:
        jour = date.min if alerte.unique else None
        if journal.alerte_inedite(alerte.cle, alerte.message, jour):
            notifieur.envoyer(alerte.message, titre="Alerte", urgent=alerte.urgente)

    for texte in suivre_conseils(journal, historiques):
        notifieur.envoyer(texte, titre="Conseil de vente", urgent=True)
    for position, avis in avis_portefeuille(conf, historiques, taux):
        if avis.action == "garder":
            continue
        # une fois par mois et par avis : le changement d'avis compte, pas sa répétition
        cle = f"avis:{avis.ticker}:{avis.action}:{date.today():%Y-%m}"
        texte = cons.formater_avis(avis, position.get("nom", avis.ticker))
        if journal.alerte_inedite(cle, texte, date.min):
            notifieur.envoyer(texte, titre="Conseil sur ta position", urgent=avis.action == "vendre")

    # Actus nouvelles sur les valeurs que tu détiens
    nouvelles = journal.nouvelles_actus([a.lien for a in actus])
    a_signaler = [a for a in actus if a.lien in nouvelles]
    if a_signaler:
        texte = "\n".join(f"• [{a.ticker}] {a.titre}\n  {a.lien}" for a in a_signaler[:10])
        notifieur.envoyer(texte, titre="Actus sur ton portefeuille")


def suivre_conseils(journal: Journal, historiques) -> list[str]:
    """Stop ou objectif atteint sur un conseil d'achat en cours : dis quoi vendre."""
    messages = []
    for c in journal.conseils_ouverts():
        cours = _dernier(historiques, c["ticker"])
        if cours is None:
            continue
        resultat = c["montant_eur"] * (cours / c["cours"] - 1) - c["frais_eur"]
        if cours <= c["stop"]:
            journal.clore_conseil(c["id"], "stop", cours)
            messages.append(
                f"🔴 Vends {c['ticker']} : le stop {c['stop']:.2f} est touché (cours {cours:.2f}).\n"
                f"Si tu as suivi le conseil du {c['cree_le'][:10]}, la perte reste limitée "
                f"(≈ {resultat:+.0f} € frais compris). Ne pas attendre un rebond fait partie de la règle."
            )
        elif cours >= c["objectif"]:
            journal.clore_conseil(c["id"], "objectif", cours)
            messages.append(
                f"💰 Objectif atteint sur {c['ticker']} ({cours:.2f}, conseil du {c['cree_le'][:10]}).\n"
                f"Vends au moins la moitié (≈ {resultat:+.0f} € sur la ligne entière, frais compris) "
                f"et remonte le stop du reste à ton prix d'achat ({c['cours']:.2f})."
            )
    return messages


def avis_portefeuille(conf, historiques, taux):
    """(position, avis) pour chaque position dont on a le cours."""
    resultats = []
    for position in conf["portefeuille"]:
        hist = historiques.get(position["ticker"])
        if hist is None:
            continue
        prix_revient = position.get("prix_revient")
        devise = position.get("devise", "EUR")
        if prix_revient and devise != "EUR":
            prix_revient = prix_revient * taux[devise] if taux.get(devise) else None
        avis = cons.avis_position(position, hist, prix_revient)
        if avis:
            resultats.append((position, avis))
    return resultats


def opportunites(conf, journal: Journal, notifieur: Notifieur) -> None:
    """Alerte immédiate quand une valeur de l'univers IA reçoit un catalyseur
    frais et que le marché confirme (score au-dessus du seuil)."""
    reglages = conf["opportunites"]
    if not reglages["activee"]:
        return
    univers = conf["theme_ia"]["univers"]
    historiques = src_cours.historiques(univers + [conf["reference"], "EURUSD=X"], periode="6mo")
    eurusd = _dernier(historiques, "EURUSD=X")
    actus = src_actus.collecter([], univers)
    # préfixe propre : le résumé du matin marque aussi les actus comme vues
    nouvelles = {l[4:] for l in journal.nouvelles_actus([f"opp:{a.lien}" for a in actus])}
    scores = opp.scorer(univers, historiques, actus, nouvelles, conf["seuils"]["volume_inhabituel_x"])

    seuil = reglages["seuil_alerte"]
    # seules les valeurs proches du seuil avec une actu fraîche coûtent un appel à Claude
    candidates = [o for o in scores if o.nouveau and o.score >= seuil - 25][:8]
    if candidates:
        opp.affiner_avec_claude(candidates, conf["ia"], reglages.get("modele"))
    retenues = sorted((o for o in candidates if o.score >= seuil), key=lambda o: o.score, reverse=True)

    reference = _dernier(historiques, conf["reference"])
    for o in retenues[: reglages["alertes_max"]]:
        if not journal.alerte_inedite(f"opportunite:{o.ticker}", f"score {o.score}"):
            continue
        texte = opp.formater(o)
        plan = conseil_achat(conf, journal, o.ticker, historiques, eurusd)
        if plan:
            texte += "\n\n" + plan
        notifieur.envoyer(texte, titre="Opportunité", urgent=True)
        # notée au journal : le bilan dira si les opportunités scorées tiennent leurs promesses
        journal.noter(
            Hypothese(o.ticker, "hausse", 20, 3 if o.score >= 80 else 2,
                      f"Opportunité {o.score}/100 : " + (o.lecture_ia or ", ".join(o.signaux)),
                      "Le catalyseur est démenti ou le cours repasse sous son niveau d'avant l'annonce.",
                      origine="opportunite"),
            o.dernier_cours, reference,
        )


def conseil_achat(conf, journal: Journal, ticker, historiques, eurusd) -> str | None:
    """Plan d'achat chiffré, noté au journal pour le suivi du stop et de l'objectif."""
    reglages = conf["conseils"]
    hist = historiques.get(ticker)
    if not reglages["actives"] or hist is None:
        return None
    taux = 1.0 if cons.devise_de(ticker) == "EUR" else eurusd
    if not taux:
        return None  # sans taux de change, le montant en euros serait faux
    plan = cons.plan_achat(ticker, hist, reglages, taux)
    if plan is None:
        return None
    if journal.ouvrir_conseil(ticker, plan.cours, plan.stop, plan.objectif, plan.montant_eur,
                              2 * reglages["frais_ordre_eur"]) is None:
        return f"(Un conseil d'achat sur {ticker} est déjà en cours : pas de nouvel achat.)"
    return plan.formater()


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

    classement = opp.scorer(theme["univers"], historiques, actus, set(),
                            conf["seuils"]["volume_inhabituel_x"])[: theme["pistes_max"]]
    taux = {"USD": _dernier(src_cours.historiques(["EURUSD=X"], periode="5d"), "EURUSD=X")}
    avis = [cons.formater_avis(a, p.get("nom", a.ticker))
            for p, a in avis_portefeuille(conf, historiques, taux)]
    notifieur.envoyer(
        formater_resume(contexte, classement, hypotheses, intros_a_suivre, avis),
        titre=f"Résumé du {date.today():%d/%m/%Y}",
    )


def formater_resume(contexte, classement, hypotheses, intros_a_suivre, avis=()) -> str:
    parties = ["🌍 Contexte", contexte or "—", ""]
    if avis:
        parties.append("📋 Conseils sur tes positions")
        parties += list(avis)
        parties.append("")
    parties.append("💎 Valeurs IA les mieux notées (score sur 100)")
    if classement:
        for o in classement:
            motifs = ([f"catalyseur {', '.join(o.catalyseurs)}"] if o.catalyseurs else []) + o.signaux
            parties.append(f"• {o.ticker} {o.score}/100 : {', '.join(motifs) or 'pas de signal fort'}")
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
    c = journal.bilan_conseils()
    if c["clos"]:
        texte += (
            f"\n\nConseils d'achat clos : {c['clos']} ({c['ouverts']} en cours)\n"
            f"Gagnants : {c['gagnants_pct']:.0f} %\n"
            f"Résultat net si tous suivis, frais compris : {c['gain_net_eur']:+.0f} €"
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
        _proteger("opportunites", opportunites, conf, journal, notifieur)
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


COMMANDES = {"veille": veille, "opportunites": opportunites, "resume": resume, "bilan": bilan, "boucle": boucle}


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
