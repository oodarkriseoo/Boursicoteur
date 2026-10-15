"""Point d'entrée : ``python -m boursicoteur <commande>``.

Commandes :
  veille  contrôle rapide (alertes de chute, conseils sur tes positions)
  opportunites  repère les valeurs de chaque thème portées par un catalyseur frais, score sur 100
  evenements  relie l'actu non boursière (monde, santé, défense, énergie) aux valeurs suivies
  portefeuille  valeur, plus-value et poids de chaque ligne et de chaque enveloppe
  resume  résumé quotidien : contexte, pistes par thème, introductions, hypothèses
  bilan   évalue les hypothèses arrivées à échéance et compare à la référence
  boucle  tourne en continu : veille toutes les N minutes, résumé et bilan aux heures prévues

L'outil ne passe aucun ordre : il informe, tu décides.
"""

from __future__ import annotations

import argparse
import logging
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import config as cfg
from . import conseils as cons
from . import evenements as evt
from .courtier import Courtier, contrat_ibkr
from . import hypotheses as hyp
from . import memoire
from . import opportunites as opp
from . import portefeuille as ptf
from . import risque as rsq
from . import signaux
from . import utilisateurs as usr
from .journal import Hypothese, Journal
from .notifications import Notifieur, chat_du_clic
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


def utilisateurs(conf, journal: Journal, notifieur: Notifieur) -> list[dict]:
    """Les utilisateurs acceptés, l'administrateur (TELEGRAM_CHAT_ID) en premier.
    À la première mise en route, l'administrateur reprend le portefeuille de config.yaml."""
    usr.preparer(journal, conf, str(notifieur.chat_id or ""))
    return usr.actifs(journal)


def lignes_surveillees(u: dict) -> list[dict]:
    """Positions de l'utilisateur, puis ses valeurs surveillées sans quantité ni prix de revient."""
    detenus = {p["ticker"] for p in u["portefeuille"]}
    return u["portefeuille"] + [{"ticker": t, "nom": t} for t in u["suivis"] if t not in detenus]


def veille(conf, journal: Journal, notifieur: Notifieur) -> None:
    membres = utilisateurs(conf, journal, notifieur)
    surveilles = list(dict.fromkeys(t for u in membres for t in usr.tickers_de(u)))
    devises = {p["devise"] for u in membres for p in u["portefeuille"] if p.get("devise", "EUR") != "EUR"}
    paires = {d: f"EUR{d}=X" for d in devises}
    suivis = [c["ticker"] for c in journal.conseils_ouverts()]
    # un seul téléchargement pour tous les utilisateurs
    historiques = src_cours.historiques(
        list(dict.fromkeys(surveilles + suivis + [conf["reference"]])) + [VIX] + list(paires.values()),
        periode="2y")
    taux = {d: _dernier(historiques, paire) for d, paire in paires.items()}

    # Actus d'abord : la dernière sur chaque valeur accompagne les alertes de chute
    actus = src_actus.collecter([], surveilles)
    titres = {}
    for a in actus:
        titres.setdefault(a.ticker, a)  # les flux listent le plus récent en premier
    cours = {t: c for t in surveilles if (c := _dernier(historiques, t)) is not None}
    precedents = journal.echanger_cours(cours)
    marche = signaux.alerte_marche(historiques.get(VIX), conf["seuils"]["vix_alerte"])

    for u in membres:
        conf_u, chat = usr.conf_de(conf, u), u["chat_id"]
        alertes = signaux.alertes_portefeuille(
            lignes_surveillees(u), historiques, conf["seuils"], {d: t for d, t in taux.items() if t},
            precedents, titres,
        ) + marche
        for alerte in alertes:
            jour = date.min if alerte.unique else None
            if journal.alerte_inedite(usr.cle_alerte(u, alerte.cle), alerte.message, jour):
                notifieur.envoyer(alerte.message, titre="Alerte", urgent=alerte.urgente, chat_id=chat)

        for texte in suivre_conseils(journal, historiques, chat):
            notifieur.envoyer(texte, titre="Conseil de vente", urgent=True, chat_id=chat)
        for position, avis in avis_portefeuille(conf_u, historiques, taux):
            if avis.action in ("garder", "réévaluer"):
                continue  # long terme : le résumé suffit, l'alerte de baisse inhabituelle veille
            # une fois par mois et par avis : le changement d'avis compte, pas sa répétition
            cle = usr.cle_alerte(u, f"avis:{avis.ticker}:{avis.action}:{date.today():%Y-%m}")
            texte = cons.formater_avis(avis, position.get("nom", avis.ticker))
            if journal.alerte_inedite(cle, texte, date.min):
                notifieur.envoyer(texte, titre="Conseil sur ta position", urgent=avis.action == "vendre",
                                  chat_id=chat)
        noter_valeur(conf_u, journal, historiques, chat_id=chat)


def suivre_conseils(journal: Journal, historiques, chat_id: str | None = None) -> list[str]:
    """Stop ou objectif atteint sur un conseil d'achat en cours : dis quoi vendre.
    ``chat_id`` : les conseils de cet utilisateur seulement (tous si None)."""
    messages = []
    for c in journal.conseils_ouverts(chat_id):
        cours = _dernier(historiques, c["ticker"])
        if cours is None:
            continue
        resultat = c["montant_eur"] * (cours / c["cours"] - 1) - c["frais_eur"]
        if c["decision"] == "ignore":
            # suivi silencieux : le bilan mesure aussi les conseils que tu as écartés
            if cours <= c["stop"] or cours >= c["objectif"]:
                journal.clore_conseil(c["id"], "stop" if cours <= c["stop"] else "objectif", cours)
            continue
        if c["decision"] == "ibkr" and (cours <= c["stop"] or cours >= c["objectif"]):
            atteint = "stop" if cours <= c["stop"] else "objectif"
            journal.clore_conseil(c["id"], atteint, cours)
            messages.append(
                f"{'🔴' if atteint == 'stop' else '💰'} {c['ticker']} a atteint son {atteint} ({cours:.2f}). "
                f"L'ordre posé chez IBKR a dû s'exécuter (≈ {resultat:+.0f} €) : vérifie dans l'appli IBKR."
            )
            continue
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
    """Alerte immédiate quand une valeur d'un thème suivi reçoit un catalyseur
    frais et que le marché confirme (score au-dessus du seuil)."""
    reglages = conf["opportunites"]
    if not reglages["activee"]:
        return
    membres = utilisateurs(conf, journal, notifieur)
    # les valeurs que chacun surveille passent par le même score que les thèmes
    univers = list(dict.fromkeys(cfg.univers_complet(conf) + [t for u in membres for t in u["suivis"]]))
    historiques = src_cours.historiques(univers + [conf["reference"], "EURUSD=X"], periode="6mo")
    eurusd = _dernier(historiques, "EURUSD=X")
    actus = src_actus.collecter([], univers)
    # préfixe propre : le résumé du matin marque aussi les actus comme vues
    nouvelles = {l[4:] for l in journal.nouvelles_actus([f"opp:{a.lien}" for a in actus])}
    scores = opp.scorer(univers, historiques, actus, nouvelles, conf["seuils"]["volume_inhabituel_x"],
                        journal.effet_par_categorie())
    themes = cfg.theme_de(conf)
    for o in scores:
        o.theme = themes.get(o.ticker, "")
    reference = _dernier(historiques, conf["reference"])
    # historique des news : chaque catalyseur frais est noté avec le cours du moment,
    # son effet sera mesuré à 1 jour, 1 semaine et 4 semaines
    for o in scores:
        for a in o.titres:
            if a.lien in nouvelles and o.dernier_cours is not None:
                journal.noter_catalyseur(a.lien, o.ticker, [n for n, _ in opp.catalyseurs_de(a)],
                                         a.titre, o.dernier_cours, reference)

    seuil = reglages["seuil_alerte"]
    # seules les valeurs proches du seuil avec une actu fraîche coûtent un appel à Claude
    candidates = [o for o in scores if o.nouveau and o.score >= seuil - 25][:8]
    if candidates:
        opp.affiner_avec_claude(candidates, conf["ia"], reglages.get("modele"),
                                memoire=memoire.fiche(journal, [o.ticker for o in candidates],
                                                      secteurs(conf)))
    retenues = sorted((o for o in candidates if o.score >= seuil), key=lambda o: o.score, reverse=True)

    for o in retenues[: reglages["alertes_max"]]:
        if not journal.alerte_inedite(f"opportunite:{o.ticker}", f"score {o.score}"):
            continue
        for u in usr.destinataires(membres, o.ticker, conf):
            conf_u = usr.conf_de(conf, u)
            texte = opp.formater(o)
            plan, id_conseil = conseil_achat(conf_u, journal, o.ticker, historiques, eurusd, u)
            if plan:
                texte += "\n\n" + plan
            notifieur.envoyer(texte, titre="Opportunité", urgent=True,
                              boutons=boutons_conseil(conf_u, o.ticker, id_conseil, u), chat_id=u["chat_id"])
        # notée au journal : le bilan dira si les opportunités scorées tiennent leurs promesses
        journal.noter(
            Hypothese(o.ticker, "hausse", 20, 3 if o.score >= 80 else 2,
                      f"Opportunité {o.score}/100 : " + (o.lecture_ia or ", ".join(o.signaux)),
                      "Le catalyseur est démenti ou le cours repasse sous son niveau d'avant l'annonce.",
                      origine="opportunite"),
            o.dernier_cours, reference,
        )


def evenements(conf, journal: Journal, notifieur: Notifieur, etat: dict | None = None) -> None:
    """Lit les sources non boursières et demande à Claude quelles valeurs suivies
    en profitent ou en souffrent, surtout en second rang. ``etat`` garde, d'un
    passage à l'autre de la boucle, le cache des flux et le compte des appels."""
    reglages = conf["evenements"]
    if not reglages["actives"]:
        return
    etat = {} if etat is None else etat
    actus = []
    for url in reglages["flux"]:
        actus += src_actus.lire_flux(url, limite=15, cache=etat.setdefault("cache", {}))
    inconnus = journal.liens_inconnus([f"evt:{a.lien}" for a in actus])
    inedites = list({a.lien: a for a in actus if f"evt:{a.lien}" in inconnus}.values())[:30]
    if not inedites:
        return
    # les actus attendent le prochain appel autorisé : rien n'est perdu, tout est groupé
    maintenant = time.monotonic()
    if etat.get("jour") != date.today():
        etat.update(jour=date.today(), appels=0)
    if (etat["appels"] >= reglages["appels_max_jour"]
            or maintenant - etat.get("dernier_appel", -1e9) < reglages["pause_ia_min"] * 60):
        return
    etat["dernier_appel"] = maintenant
    etat["appels"] += 1

    membres = utilisateurs(conf, journal, notifieur)
    valeurs = cfg.theme_de(conf)
    # un seul appel pour tous : les valeurs de chacun s'ajoutent à la liste, sans dire qui
    # les détient (le libellé figure dans l'alerte que d'autres utilisateurs peuvent recevoir)
    for u in membres:
        for t in usr.tickers_de(u):
            valeurs.setdefault(t, "valeur suivie")
    donnees = evt.preparer(inedites, valeurs, memoire.fiche(journal, surcharges=secteurs(conf), max_lignes=25))
    resultat = hyp.avec_claude(donnees, conf["ia"], schema=evt.SCHEMA, consignes=evt.CONSIGNES,
                               modele=reglages.get("modele"))
    if resultat is None:
        return  # Claude indisponible : les actus seront reproposées au prochain appel
    journal.nouvelles_actus([f"evt:{a.lien}" for a in inedites])
    effets = evt.lire_reponse(resultat, inedites, valeurs)
    if not effets:
        return

    historiques = src_cours.historiques(list({e.ticker for e in effets}) + [conf["reference"], "EURUSD=X"],
                                        periode="6mo")
    reference = _dernier(historiques, conf["reference"])
    eurusd = _dernier(historiques, "EURUSD=X")
    retenus = []
    for e in effets:
        cours = _dernier(historiques, e.ticker)
        if cours is None:
            continue
        # notée deux fois : effet mesuré à 1 j, 1 sem., 4 sem., et hypothèse évaluée à son échéance
        journal.noter_catalyseur(f"{e.actu.lien}#{e.ticker}", e.ticker, [e.categorie], e.actu.titre,
                                 cours, reference)
        journal.noter(Hypothese(e.ticker, e.sens, e.delai_jours, e.confiance,
                                f"Événement {e.rang} : {e.chaine}", e.invalidation, origine="evenement"),
                      cours, reference)
        retenus.append(e)
    alertes = [e for e in retenus if e.confiance >= reglages["confiance_alerte"]]
    for e in alertes[: reglages["alertes_max"]]:
        if not journal.alerte_inedite(f"evenement:{e.ticker}:{e.actu.lien}", e.chaine):
            continue
        for u in usr.destinataires(membres, e.ticker, conf):
            conf_u = usr.conf_de(conf, u)
            texte, id_conseil = evt.formater(e), None
            if e.sens == "hausse":
                # même plan chiffré que pour une opportunité : stop, objectif, montant borné par la poche
                plan, id_conseil = conseil_achat(conf_u, journal, e.ticker, historiques, eurusd, u)
                if plan:
                    texte += "\n\n" + plan
            elif e.ticker in {p["ticker"] for p in u["portefeuille"]}:
                texte += "\n\n⚠️ Tu détiens cette valeur : vérifie ton stop et décide à froid si tu allèges."
            notifieur.envoyer(texte, titre="Événement", urgent=e.confiance >= 4,
                              boutons=boutons_conseil(conf_u, e.ticker, id_conseil, u), chat_id=u["chat_id"])


def ibkr_pour(conf, u: dict | None) -> bool:
    """Le courtier branché ne sert qu'à l'administrateur : c'est son compte."""
    return (u is None or u["statut"] == usr.ADMIN) and Courtier(conf["courtier"]).actif


def boutons_conseil(conf, ticker, id_conseil, u: dict | None = None):
    """Boutons sous un conseil d'achat : déjà acheté, ignorer, et IBKR s'il est branché."""
    if not id_conseil:
        return None
    boutons = [[("✅ Déjà acheté", f"achete:{id_conseil}"), ("❌ Ignorer", f"ignore:{id_conseil}")]]
    if ibkr_pour(conf, u) and contrat_ibkr(ticker):
        boutons.insert(0, [("🛒 Acheter via IBKR", f"commande:{id_conseil}")])
    return boutons


def conseil_achat(conf, journal: Journal, ticker, historiques, eurusd,
                  u: dict | None = None) -> tuple[str | None, int | None]:
    """Plan d'achat chiffré, noté au journal pour le suivi du stop et de l'objectif.
    Renvoie le texte et l'identifiant du conseil (pour les boutons). ``u`` :
    l'utilisateur à qui il est donné (l'administrateur si None)."""
    reglages = conf["conseils"]
    hist = historiques.get(ticker)
    if not reglages["actives"] or hist is None:
        return None, None
    taux = 1.0 if cons.devise_de(ticker) == "EUR" else eurusd
    if not taux:
        return None, None  # sans taux de change, le montant en euros serait faux
    # IBKR ne prend que des actions entières par l'API
    plan = cons.plan_achat(ticker, hist, reglages, taux, fractionnable=not ibkr_pour(conf, u))
    if plan is None:
        return None, None
    id_conseil = journal.ouvrir_conseil(ticker, plan.cours, plan.stop, plan.objectif,
                                        plan.montant_eur, 2 * reglages["frais_ordre_eur"],
                                        quantite=plan.quantite, chat_id=u["chat_id"] if u else "")
    if id_conseil is None:
        return f"(Un conseil d'achat sur {ticker} est déjà en cours : pas de nouvel achat.)", None
    return plan.formater(), id_conseil


BOUTON_ACTUS = ("📰 En savoir plus : actus de mes positions", "actus:portefeuille")
BOUTON_PORTEFEUILLE = ("💼 Détail du portefeuille", "valeur:portefeuille")


def etat_portefeuille(positions, historiques=None) -> ptf.Etat:
    """Valeur du portefeuille en euros ; récupère les cours manquants et les taux de change."""
    devises = {p["devise"] for p in positions if p.get("devise", "EUR") != "EUR"}
    paires = {d: f"EUR{d}=X" for d in devises}
    historiques = dict(historiques or {})
    manquants = [t for t in [p["ticker"] for p in positions] + list(paires.values()) if t not in historiques]
    if manquants:
        historiques.update(src_cours.historiques(list(dict.fromkeys(manquants)), periode="5d"))
    return ptf.valoriser(positions, historiques, {d: _dernier(historiques, paire) for d, paire in paires.items()})


def detail_portefeuille(etat: ptf.Etat, journal: Journal, reference: str, chat_id: str = "") -> str:
    performance = ptf.formater_performance(journal.photos(chat_id), reference)
    return ptf.formater(etat) + (f"\n\n{performance}" if performance else "")


def noter_valeur(conf, journal: Journal, historiques, jour: date | None = None, chat_id: str = "") -> None:
    """Photo du jour de la valeur du portefeuille, avec le cours de la référence.
    Refaite à chaque veille : la dernière de la journée l'emporte."""
    reference = _dernier(historiques, conf["reference"])
    if not conf["portefeuille"] or reference is None:
        return
    jour = jour or date.today()
    etat = etat_portefeuille(conf["portefeuille"], historiques)
    if not etat.lignes:
        return
    photo = ptf.photo(etat, journal.photo_avant(jour, chat_id))
    if photo is not None:
        journal.photographier(jour, etat.valeur_eur, photo[0], reference, photo[1], chat_id)


def portefeuille(conf, journal: Journal, notifieur: Notifieur) -> None:
    """Commande en ligne : le portefeuille de l'administrateur."""
    admin = next(u for u in utilisateurs(conf, journal, notifieur) if u["statut"] == usr.ADMIN)
    notifieur.envoyer(detail_portefeuille(etat_portefeuille(admin["portefeuille"]), journal, conf["reference"],
                                          admin["chat_id"]), titre="Ton portefeuille")


def actus_portefeuille(portefeuille, collecter=src_actus.collecter, par_valeur: int = 3) -> str:
    """Dernières actus de chaque position, avec leurs liens. Récoltées au clic
    seulement : aucun appel à l'IA, et rien n'encombre le fil le reste du temps."""
    actus = collecter([], [p["ticker"] for p in portefeuille])
    blocs = []
    for p in portefeuille:
        siennes = [a for a in actus if a.ticker == p["ticker"]][:par_valeur]
        if siennes:
            blocs.append(f"{p.get('nom', p['ticker'])} ({p['ticker']})\n"
                         + "\n".join(f"• {a.titre}\n  {a.lien}" for a in siennes))
    return "\n\n".join(blocs) or "Aucune actu récente sur tes positions."


def ticker_existe(ticker: str) -> bool:
    """Vrai si Yahoo Finance donne des cours pour ce ticker."""
    return ticker in src_cours.historiques([ticker], periode="5d")


def liste_themes(conf, u: dict) -> tuple[str, list]:
    """Les domaines suivis par le bot, avec un bouton par domaine pour voir ses valeurs."""
    themes = {c: t for c, t in cfg.themes(conf).items() if t["univers"]}
    siens = usr.domaines_de(conf, u)
    lignes = [f"{'🔔' if cle in siens else '🔕'} {t['libelle']} : {len(t['univers'])} valeurs"
              for cle, t in themes.items()]
    texte = ("Domaines suivis par le bot. 🔔 : tu reçois leurs opportunités et leur classement "
             "dans le résumé du matin" + (" (tous, tant que tu n'as rien choisi)" if u.get("themes") is None else "")
             + ".\n\n" + "\n".join(lignes)
             + "\n\nTouche un domaine pour voir ses valeurs les mieux notées aujourd'hui et choisir s'il t'intéresse.")
    boutons = [(f"📊 {t['libelle']}", f"theme:{cle}") for cle, t in themes.items()]
    return texte, [boutons[i:i + 2] for i in range(0, len(boutons), 2)]


def detail_theme(conf, journal: Journal, u: dict, cle: str, telecharger=src_cours.historiques,
                 collecter=src_actus.collecter, nombre: int = 10) -> tuple[str, list | None]:
    """Valeurs d'un domaine classées par leur score du jour, avec les boutons
    pour suivre le domaine et surveiller ses meilleures valeurs."""
    theme = cfg.themes(conf).get(cle)
    if not theme or not theme["univers"]:
        return f"Domaine inconnu : {cle}. /themes pour la liste.", None
    historiques = telecharger(theme["univers"], periode="6mo")
    actus = collecter([], theme["univers"])
    scores = opp.scorer(theme["univers"], historiques, actus, set(), conf["seuils"]["volume_inhabituel_x"],
                        journal.effet_par_categorie())[:nombre]
    lignes = []
    for i, o in enumerate(scores, 1):
        motifs = ([f"catalyseur {', '.join(o.catalyseurs)}"] if o.catalyseurs else []) + o.signaux[:3]
        pea = " · PEA possible" if cons.enveloppe_de(o.ticker) == "PEA possible" else ""
        lignes.append(f"{i}. {o.ticker}{pea} : {o.score}/100, {', '.join(motifs) or 'pas de signal fort'}")
    sans_cours = [t for t in theme["univers"] if t not in historiques]
    texte = (f"📊 {theme['libelle']} : valeurs les mieux notées aujourd'hui (score sur 100)\n"
             + ("\n".join(lignes) or "Aucun cours disponible pour l'instant.")
             + (f"\nSans cours aujourd'hui : {', '.join(sans_cours)}" if sans_cours else "")
             + "\n\nUn score élevé veut dire un catalyseur récent confirmé par le marché, pas une "
               "garantie de hausse. /suivre TICKER pour surveiller une valeur.")
    suivi = cle in usr.domaines_de(conf, u)
    boutons = [[("🔕 Ne plus suivre ce domaine" if suivi else "🔔 Suivre ce domaine", f"interet:{cle}")]]
    meilleures = [o.ticker for o in scores[:3] if o.ticker not in u["suivis"]]
    if meilleures:
        boutons.append([(f"➕ Surveiller {', '.join(meilleures)}", f"suivre:{','.join(meilleures)}"[:64])])
    return texte, boutons


def traiter_mises_a_jour(conf, journal: Journal, notifieur: Notifieur, offset, attente: int,
                         courtier: Courtier | None = None, collecter=src_actus.collecter,
                         valoriser=etat_portefeuille, valider=ticker_existe, telecharger=src_cours.historiques):
    """Traite les commandes texte de chaque utilisateur (/ajouter, /suivre…) et les
    boutons : accès au bot, actus et détail des positions, déjà acheté, ignorer,
    acheter via IBKR puis confirmer. Renvoie le nouvel offset Telegram."""
    clics, messages, offset = notifieur.mises_a_jour(offset, attente)
    admin_id = str(notifieur.chat_id or "")
    for message in messages:
        chat = str(message["chat"]["id"])
        auteur = message.get("from") or {}
        nom = " ".join(filter(None, (auteur.get("first_name"), auteur.get("last_name")))) \
            or auteur.get("username") or chat
        texte = message["text"]
        u = journal.utilisateur(chat)
        mots = texte.split()
        ordre = mots[0].split("@")[0].lower() if mots else ""
        if usr.est_actif(u) and ordre == "/portefeuille":
            notifieur.envoyer(detail_portefeuille(valoriser(u["portefeuille"]), journal, conf["reference"], chat)
                              if u["portefeuille"] else "Aucune position : /ajouter pour commencer.",
                              titre="Ton portefeuille", chat_id=chat)
            continue
        if usr.est_actif(u) and ordre == "/themes":
            if len(mots) > 1:
                reponse, boutons = detail_theme(conf, journal, u, mots[1].lower(), telecharger, collecter)
            else:
                reponse, boutons = liste_themes(conf, u)
            notifieur.envoyer(reponse, titre="", boutons=boutons, chat_id=chat)
            continue
        for destinataire, reponse, boutons in usr.commande(journal, chat, nom, texte, admin_id, valider,
                                                          conf["utilisateurs"]["max"]):
            notifieur.envoyer(reponse, titre="", boutons=boutons, chat_id=destinataire)

    for clic in clics:
        chat = chat_du_clic(clic)
        u = journal.utilisateur(chat)
        action, _, id_texte = str(clic.get("data", "")).partition(":")
        if not usr.est_actif(u):
            # un inconnu qui trouverait le bot ne peut rien déclencher
            notifieur.accuser_clic(clic, "Accès non autorisé.", message=False)
            continue
        if action in ("accepter", "refuser"):
            reponse = usr.decider_acces(journal, id_texte, action == "accepter") if u["statut"] == usr.ADMIN else None
            notifieur.accuser_clic(clic, ("✅ Accès accepté." if action == "accepter" else "❌ Accès refusé.")
                                   if reponse else "Demande déjà traitée.")
            if reponse:
                notifieur.envoyer(reponse[1], titre="", chat_id=reponse[0])
            continue
        if action == "quitter":
            if id_texte == chat and u["statut"] != usr.ADMIN:
                journal.oublier_utilisateur(chat)
                notifieur.accuser_clic(clic, "🗑️ Tes données sont effacées. /start pour revenir.")
            else:
                notifieur.accuser_clic(clic, "Rien n'a été effacé.")
            continue
        if action == "theme":
            notifieur.accuser_clic(clic, "Je calcule les scores…", message=False, retirer=False)
            reponse, boutons = detail_theme(conf, journal, u, id_texte, telecharger, collecter)
            notifieur.envoyer(reponse, titre="", boutons=boutons, chat_id=chat)
            continue
        if action == "interet":
            if id_texte in cfg.themes(conf):
                libelle = cfg.themes(conf)[id_texte]["libelle"]
                suivi = usr.basculer_domaine(journal, conf, u, id_texte)
                notifieur.accuser_clic(clic, f"🔔 Tu suis {libelle}." if suivi else
                                       f"🔕 Tu ne reçois plus les opportunités de {libelle}.")
            else:
                notifieur.accuser_clic(clic, "Domaine inconnu.")
            continue
        if action == "suivre":
            # seules les valeurs des thèmes peuvent arriver par ce bouton
            permis = set(cfg.univers_complet(conf))
            ajouts = [t for t in id_texte.split(",") if t in permis and t not in u["suivis"]]
            ajouts = ajouts[: max(0, usr.SUIVIS_MAX - len(u["suivis"]))]
            u["suivis"] += ajouts
            journal.enregistrer_utilisateur(u)
            notifieur.accuser_clic(clic, f"➕ Surveillées : {', '.join(ajouts)}." if ajouts else "Rien à ajouter.")
            continue
        if action == "actus":
            # le bouton reste sous le résumé : chaque clic récolte des actus fraîches
            notifieur.accuser_clic(clic, "Je rassemble les actus…", message=False, retirer=False)
            notifieur.envoyer(actus_portefeuille(u["portefeuille"], collecter), titre="Actus de tes positions",
                              chat_id=chat)
            continue
        if action == "valeur":
            notifieur.accuser_clic(clic, "Je calcule…", message=False, retirer=False)
            notifieur.envoyer(detail_portefeuille(valoriser(u["portefeuille"]), journal, conf["reference"], chat),
                              titre="Ton portefeuille", chat_id=chat)
            continue
        conseil = journal.conseil(int(id_texte)) if id_texte.isdigit() else None
        if conseil is None or conseil["chat_id"] != chat or action not in ("achete", "ignore", "commande", "confirme"):
            notifieur.accuser_clic(clic, "Bouton inconnu ou expiré.")
        elif conseil["decision"] in ("achete", "ibkr") or conseil["statut"] != "ouvert":
            notifieur.accuser_clic(clic, f"Conseil {conseil['ticker']} déjà traité ou clos.")
        elif action == "achete":
            journal.decider_conseil(conseil["id"], "achete")
            notifieur.accuser_clic(clic, f"✅ Achat {conseil['ticker']} noté. Je te préviens au stop "
                                         f"{conseil['stop']:.2f} ou à l'objectif {conseil['objectif']:.2f}.")
        elif action == "ignore":
            journal.decider_conseil(conseil["id"], "ignore")
            notifieur.accuser_clic(clic, f"❌ {conseil['ticker']} ignoré. Je le suis quand même pour le "
                                         "bilan, sans t'alerter.")
        elif courtier is None or not courtier.actif or u["statut"] != usr.ADMIN:
            notifieur.accuser_clic(clic, "Aucun courtier branché : passe l'ordre à la main.")
        elif datetime.now() - datetime.fromisoformat(conseil["cree_le"]) > timedelta(hours=24):
            notifieur.accuser_clic(clic, "Conseil de plus de 24 h : les prix ont bougé, ordre refusé.")
        elif action == "commande":
            notifieur.accuser_clic(clic, "Vérifie le récapitulatif avant de confirmer.", message=False)
            notifieur.envoyer(
                f"Acheter {conseil['quantite']:g} {conseil['ticker']} via IBKR ?\n"
                f"• Achat à cours limité, au plus {conseil['cours'] * 1.01:.2f}\n"
                f"• Stop {conseil['stop']:.2f}, objectif {conseil['objectif']:.2f}, posés chez IBKR\n"
                f"• Environ {conseil['montant_eur']:.0f} €",
                titre="Confirmer l'ordre",
                boutons=[("✔️ Confirmer", f"confirme:{conseil['id']}"), ("Annuler", f"ignore:{conseil['id']}")],
                chat_id=chat,
            )
        else:  # confirme
            resultat = courtier.acheter(
                conseil["ticker"], conseil["quantite"] or 0, conseil["cours"] * 1.01, conseil["objectif"],
                conseil["stop"], conseil["montant_eur"], journal.engage_ibkr(),
            )
            if resultat.ok:
                journal.decider_conseil(conseil["id"], "ibkr")
            notifieur.accuser_clic(clic, resultat.message)
    return offset


def resume(conf, journal: Journal, notifieur: Notifieur) -> None:
    """Un seul appel à Claude pour tous, puis un résumé par utilisateur avec ses positions."""
    membres = utilisateurs(conf, journal, notifieur)
    tickers = list(dict.fromkeys([t for u in membres for t in usr.tickers_de(u)]
                                 + cfg.univers_complet(conf) + [conf["reference"]]))
    historiques = src_cours.historiques(tickers + [VIX], periode="2y")
    actus = src_actus.collecter(conf["flux_rss"], tickers)
    journal.nouvelles_actus([a.lien for a in actus])

    themes = cfg.themes(conf)
    pistes = [p for theme in themes.values() for p in signaux.scanner_theme_ia(
        theme["univers"], historiques, actus, theme["mots_cles"],
        conf["seuils"]["volume_inhabituel_x"], theme["pistes_max"],
    )]

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
    donnees += "\n\n" + memoire.fiche(journal, surcharges=secteurs(conf))
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

    journal.mesurer_catalyseurs({t: c for t in historiques if (c := _dernier(historiques, t)) is not None},
                                reference)
    effets = journal.effet_par_categorie()
    classement = {cle: opp.scorer(theme["univers"], historiques, actus, set(),
                                  conf["seuils"]["volume_inhabituel_x"], effets)[: theme["pistes_max"]]
                  for cle, theme in themes.items() if theme["univers"]}
    taux = {"USD": _dernier(src_cours.historiques(["EURUSD=X"], periode="5d"), "EURUSD=X")}
    for u in membres:
        conf_u = usr.conf_de(conf, u)
        avis = [cons.formater_avis(a, p.get("nom", a.ticker))
                for p, a in avis_portefeuille(conf_u, historiques, taux)]
        avis += thermometre(conf_u, historiques)
        valeur = ptf.synthese(etat_portefeuille(u["portefeuille"], historiques)) if u["portefeuille"] else []
        # chacun voit le classement de ses domaines (/themes) et de ses valeurs surveillées
        siens = {themes[cle]["libelle"]: classement[cle] for cle in usr.domaines_de(conf, u)}
        if u["suivis"]:
            siens = {**classement, "Tes valeurs surveillées": opp.scorer(
                u["suivis"], historiques, actus, set(), conf["seuils"]["volume_inhabituel_x"], effets)}
        # chacun ne voit que les hypothèses sur ses domaines et sur ses propres valeurs
        vues = usr.visibles(conf, u) | {conf["reference"]}
        visibles = [h for h in hypotheses if h.ticker in vues]
        notifieur.envoyer(
            formater_resume(contexte, siens, visibles, intros_a_suivre, avis, valeur),
            titre=f"Résumé du {date.today():%d/%m/%Y}",
            boutons=[[BOUTON_PORTEFEUILLE], [BOUTON_ACTUS]] if u["portefeuille"] else None,
            chat_id=u["chat_id"],
        )


def thermometre(conf, historiques) -> list[str]:
    """Risque de baisse inhabituelle (sur 100) du marché et de chaque position."""
    lignes = []
    noms = [(conf["reference"], f"Marché ({conf['reference']})")]
    noms += [(p["ticker"], p.get("nom", p["ticker"])) for p in conf["portefeuille"]]
    noms += [(t, t) for t in conf.get("suivis", [])]
    for ticker, nom in dict(noms).items():
        hist = historiques.get(ticker)
        r = rsq.evaluer(ticker, hist) if hist is not None else None
        if r is None:
            continue
        motif = r.raisons[0] if r.raisons else "baisse dans la norme de la valeur"
        lignes.append(f"🌡️ {nom} : risque {r.score}/100, {motif}")
    return lignes


def secteurs(conf) -> dict[str, str]:
    """Secteur de chaque valeur pour la mémoire : la configuration d'abord, puis
    le tableau de memoire.py, puis le thème qui la suit."""
    par_theme = {t: lib for t, lib in cfg.theme_de(conf).items() if t not in memoire.SECTEURS}
    return {**par_theme, **conf["secteurs"]}


def formater_resume(contexte, classement: dict[str, list], hypotheses, intros_a_suivre, avis=(),
                    valeur=()) -> str:
    parties = []
    if valeur:
        parties += list(valeur) + [""]
    parties += ["🌍 Contexte", contexte or "—", ""]
    if avis:
        parties.append("📋 Conseils sur tes positions")
        parties += list(avis)
        parties.append("")
    parties.append("💎 Valeurs les mieux notées par thème (score sur 100)")
    if any(classement.values()):
        for libelle, valeurs in classement.items():
            if not valeurs:
                continue
            parties.append(libelle)
            for o in valeurs:
                motifs = ([f"catalyseur {', '.join(o.catalyseurs)}"] if o.catalyseurs else []) + o.signaux
                pea = " (PEA possible)" if cons.enveloppe_de(o.ticker) == "PEA possible" else ""
                parties.append(f"• {o.ticker}{pea} {o.score}/100 : {', '.join(motifs) or 'pas de signal fort'}")
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
    if stats["evaluees"]:
        noms = {"ia": "résumé de Claude", "regles": "règles simples", "opportunite": "opportunités scorées",
                "evenement": "actus non boursières"}
        lignes = []
        for origine, nom in noms.items():
            o = journal.bilan(origine)
            if o["evaluees"]:
                lignes.append(f"• {nom} : {o['evaluees']} évaluées, {o['taux_reussite_pct']:.0f} % réussies, "
                              f"gain moyen {o['gain_moyen_pct']:+.2f} %")
        if lignes:
            texte += "\n\nPar origine :\n" + "\n".join(lignes)
    texte += _effets_des_news(journal)
    for u in utilisateurs(conf, journal, notifieur):
        notifieur.envoyer(texte + bilan_personnel(conf, journal, u["chat_id"]), titre="Bilan des hypothèses",
                          chat_id=u["chat_id"])


def bilan_personnel(conf, journal: Journal, chat_id: str) -> str:
    """Partie du bilan propre à un utilisateur : ses conseils d'achat et son portefeuille."""
    texte = ""
    c = journal.bilan_conseils(chat_id)
    if c["clos"]:
        texte += (
            f"\n\nConseils d'achat clos : {c['clos']} ({c['ouverts']} en cours)\n"
            f"Gagnants : {c['gagnants_pct']:.0f} %\n"
            f"Résultat net si tous suivis, frais compris : {c['gain_net_eur']:+.0f} €"
        )
        if c["suivis"]:
            texte += f"\nSur ceux que tu as achetés ({c['suivis']}) : {c['gain_net_suivis_eur']:+.0f} €"
    performance = ptf.formater_performance(journal.photos(chat_id), conf["reference"])
    if performance:
        texte += f"\n\n{performance}"
    return texte


def _effets_des_news(journal: Journal) -> str:
    texte = ""
    effets = journal.effet_par_categorie()
    if effets:
        texte += "\n\nEffet des news sur 1 semaine, face au marché :"
        for categorie, e in sorted(effets.items(), key=lambda x: -x[1]["moyenne"]):
            texte += (f"\n• {categorie} : {e['moyenne']:+.1f} pts en moyenne, "
                      f"{e['positifs_pct']:.0f} % de cas gagnants ({e['cas']} cas)")
        texte += f"\nEn dessous de {opp.CAS_MINIMUM} cas, ces chiffres ne corrigent pas encore le score."
    return texte


def boucle(conf, journal: Journal, notifieur: Notifieur) -> None:
    plan = conf["planification"]
    intervalle = int(plan["intervalle_veille_min"]) * 60
    heure, minute = (int(x) for x in str(plan["heure_resume"]).split(":"))
    dernier_resume = dernier_bilan = None
    offset = None
    etat_evenements: dict = {}
    pas_evenements = int(conf["evenements"]["intervalle_min"]) * 60
    prochains_evenements = time.monotonic()
    log.info("Boucle démarrée : veille toutes les %s min.", plan["intervalle_veille_min"])
    utilisateurs(conf, journal, notifieur)
    notifieur.declarer_commandes(COMMANDES_TELEGRAM)
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
        # entre deux veilles : actus non boursières toutes les quelques minutes,
        # et écoute des boutons Telegram pour répondre vite
        fin = time.monotonic() + intervalle
        while (reste := fin - time.monotonic()) > 0:
            if time.monotonic() >= prochains_evenements:
                _proteger("evenements", evenements, conf, journal, notifieur, etat_evenements)
                prochains_evenements = time.monotonic() + pas_evenements
                continue
            attente = max(1, min(50, reste, prochains_evenements - time.monotonic()))
            if notifieur.token and notifieur.chat_id:
                try:
                    offset = traiter_mises_a_jour(conf, journal, notifieur, offset, int(attente),
                                                  Courtier(conf["courtier"]))
                except Exception:
                    log.exception("Échec du traitement d'un clic.")
                    time.sleep(5)
            else:
                time.sleep(attente)


def _proteger(nom, action, *args) -> None:
    """Une erreur ponctuelle (réseau, source en panne) ne doit pas arrêter la boucle."""
    try:
        action(*args)
    except Exception:
        log.exception("Échec de « %s », nouvel essai au prochain passage.", nom)


COMMANDES_TELEGRAM = [
    ("portefeuille", "Valeur et plus-value de tes positions"),
    ("positions", "Tes positions enregistrées"),
    ("ajouter", "TICKER QUANTITÉ PRIX [PEA|CTO] [nom]"),
    ("retirer", "TICKER : retirer une position"),
    ("stop", "TICKER PRIX : seuil de sortie"),
    ("suivre", "TICKER… : surveiller sans détenir"),
    ("oublier", "TICKER : ne plus surveiller"),
    ("liste", "Valeurs surveillées"),
    ("themes", "Domaines suivis et leurs meilleures valeurs"),
    ("poche", "MONTANT : argent dédié aux conseils d'achat"),
    ("aide", "Toutes les commandes"),
    ("quitter", "Effacer tes données"),
]

COMMANDES = {"veille": veille, "opportunites": opportunites, "evenements": evenements,
             "portefeuille": portefeuille, "resume": resume,
             "bilan": bilan, "boucle": boucle}


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
