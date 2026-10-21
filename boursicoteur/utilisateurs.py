"""Plusieurs utilisateurs sur un même bot Telegram.

Chaque conversation Telegram (chat id) est un utilisateur avec son propre
portefeuille, sa liste de valeurs surveillées et sa poche de conseils, gardés
dans la base. Les cours, les actus, le score des thèmes et les appels à Claude
restent communs : un utilisateur de plus ne coûte presque rien en API.

L'administrateur est la conversation de TELEGRAM_CHAT_ID. À la première
mise en route, il reprend le portefeuille de config.yaml ; ensuite tout se
règle depuis Telegram. Un inconnu qui écrit /start attend que l'administrateur
l'accepte. Les ordres IBKR restent réservés à l'administrateur.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from . import annuaire
from . import config as cfg
from . import conseils as cons

log = logging.getLogger(__name__)

ADMIN, ACTIF, EN_ATTENTE, REFUSE = "admin", "actif", "en_attente", "refuse"
POSITIONS_MAX = 40
SUIVIS_MAX = 30
DEMANDES_MAX = 5  # demandes d'accès en attente : au-delà, les suivantes sont refusées d'office
ENVELOPPES = ("PEA", "CTO", "PEA-PME", "AV", "CRYPTO")

AIDE = """Commandes :
/portefeuille : valeur, plus-value et poids de chaque ligne
/positions : tes positions telles qu'enregistrées
/ajouter TICKER QUANTITÉ PRIX_DE_REVIENT [PEA|CTO] [nom]
   ex. /ajouter MC.PA 3 612,40 PEA LVMH
   Prix de revient unitaire en euros, même pour une action US.
   Un ticker déjà présent dans la même enveloppe est remplacé.
/retirer TICKER [enveloppe]
/stop TICKER PRIX : alerte si le cours passe sous ce prix (0 pour l'enlever)
/suiveur TICKER POURCENT : stop suiveur, alerte si le cours recule de ce
   pourcentage depuis son plus haut (0 pour l'enlever), ex. /suiveur MC.PA 12
/baisse TICKER POURCENT : alerte dès que la valeur perd ce pourcentage sur la
   séance (0 pour l'enlever), ex. /baisse WLD.PA 1 pour le MSCI World
   Ces trois seuils marchent aussi sur une valeur que tu ne détiens pas :
   elle est alors ajoutée à tes valeurs surveillées.
/etf TICKER : marque un ETF, exempté du plafond de poids par titre (bascule)
/chercher MOTS : trouver des valeurs par nom, secteur ou pays
   ex. /chercher defense france, /chercher luxe, /chercher nvidia
/suivre TICKER… : surveiller des valeurs sans les détenir
/oublier TICKER : ne plus surveiller
/liste : valeurs surveillées
/themes : domaines suivis (IA, santé, défense…), leurs valeurs les mieux notées,
   et le choix de ceux qui t'intéressent
/poche MONTANT : argent dédié aux conseils d'achat (taille des plans)
/quitter : effacer toutes tes données

Les tickers sont ceux de Yahoo Finance : MC.PA pour LVMH à Paris, ASML.AS à
Amsterdam, NVDA sans suffixe pour les US, BTC-EUR pour le bitcoin.
Ce bot informe, il ne passe aucun ordre à ta place."""

AIDE_ADMIN = """
Administrateur :
/utilisateurs : qui utilise le bot (noms et nombre de lignes, sans le détail)"""


def est_actif(u: dict | None) -> bool:
    return bool(u) and u["statut"] in (ADMIN, ACTIF)


def cle_alerte(u: dict, cle: str) -> str:
    """Les alertes déjà envoyées se retiennent par utilisateur. L'administrateur
    garde les clés d'avant, pour ne pas recevoir deux fois les mêmes alertes."""
    return cle if u["statut"] == ADMIN else f"{u['chat_id']}:{cle}"


def conf_de(conf: dict[str, Any], u: dict) -> dict[str, Any]:
    """La configuration vue par un utilisateur : son portefeuille, ses valeurs
    surveillées et sa poche ; tout le reste est commun."""
    reglages = dict(conf["conseils"])
    if u.get("poche_eur"):
        reglages["poche_eur"] = u["poche_eur"]
    return {**conf, "portefeuille": u["portefeuille"], "suivis": u["suivis"], "conseils": reglages}


def preparer(journal, conf: dict[str, Any], admin_id: str) -> None:
    """Crée l'administrateur à la première mise en route, avec le portefeuille de
    config.yaml, et lui rattache les conseils et photos déjà au journal."""
    if journal.utilisateur(admin_id) is None:
        journal.enregistrer_utilisateur({"chat_id": admin_id, "nom": "admin", "statut": ADMIN,
                                         "portefeuille": conf["portefeuille"], "suivis": []})
        log.info("Administrateur créé avec les %d positions de config.yaml ; elles se gèrent "
                 "désormais depuis Telegram.", len(conf["portefeuille"]))
    journal.attribuer(admin_id)


def actifs(journal) -> list[dict]:
    return journal.utilisateurs(ADMIN, ACTIF)


def tickers_de(u: dict) -> list[str]:
    return list(dict.fromkeys([p["ticker"] for p in u["portefeuille"]] + u["suivis"]))


def domaines_de(conf: dict[str, Any], u: dict) -> list[str]:
    """Les thèmes (clés de config) qui intéressent l'utilisateur ; tous s'il n'a rien choisi."""
    tous = [cle for cle, t in cfg.themes(conf).items() if t["univers"]]
    return tous if u.get("themes") is None else [c for c in tous if c in u["themes"]]


def visibles(conf: dict[str, Any], u: dict) -> set[str]:
    """Valeurs sur lesquelles l'utilisateur reçoit opportunités et hypothèses :
    celles de ses domaines, ses positions et ses valeurs surveillées."""
    themes = cfg.themes(conf)
    return {t for cle in domaines_de(conf, u) for t in themes[cle]["univers"]} | set(tickers_de(u))


def destinataires(utilisateurs: list[dict], ticker: str, conf: dict[str, Any]) -> list[dict]:
    """Qui prévenir pour une valeur : ceux dont un domaine la contient, et ceux
    qui la détiennent ou la surveillent."""
    return [u for u in utilisateurs if ticker in visibles(conf, u)]


def basculer_domaine(journal, conf: dict[str, Any], u: dict, cle: str) -> bool:
    """Ajoute ou retire un domaine ; renvoie True s'il est suivi après le clic."""
    tous = [c for c, t in cfg.themes(conf).items() if t["univers"]]
    suivis = domaines_de(conf, u)
    suivis = [c for c in suivis if c != cle] if cle in suivis else suivis + [cle]
    u["themes"] = None if set(suivis) == set(tous) else [c for c in tous if c in suivis]
    journal.enregistrer_utilisateur(u)
    return cle in suivis


def _nombre(texte: str) -> float | None:
    try:
        return float(texte.replace(",", ".").replace("€", ""))
    except ValueError:
        return None


def _regler(u: dict, ticker: str, cle: str, valeur: float,
            valider: Callable[[str], bool]) -> str | None:
    """Pose (ou enlève si ``valeur`` vaut 0) un seuil d'alerte sur une position,
    ou à défaut sur une valeur surveillée, ajoutée à la liste si besoin.
    Renvoie le message d'erreur, ou None si c'est fait."""
    visees = [p for p in u["portefeuille"] if p["ticker"] == ticker]
    if not visees:
        if ticker not in u["suivis"]:
            if not valeur:
                return f"{ticker} n'est ni dans tes positions ni dans tes valeurs surveillées."
            if len(u["suivis"]) >= SUIVIS_MAX:
                return f"{SUIVIS_MAX} valeurs surveillées au plus : retires-en une avec /oublier."
            if not valider(ticker):
                return (f"Pas de cours pour {ticker} sur Yahoo Finance. Vérifie le suffixe de la place "
                        "(MC.PA pour Paris, ASML.AS pour Amsterdam, rien pour les US).")
            u["suivis"].append(ticker)
        reglages = u.setdefault("alertes_suivis", {})
        visees = [reglages.setdefault(ticker, {})]
    for p in visees:
        if valeur:
            p[cle] = valeur
        else:
            p.pop(cle, None)
    if not u.get("alertes_suivis", {}).get(ticker, True):
        del u["alertes_suivis"][ticker]
    return None


def _seuils_suivi(reglages: dict) -> str:
    morceaux = []
    if reglages.get("stop"):
        morceaux.append(f"sous {reglages['stop']:g}")
    if reglages.get("suiveur_pct"):
        morceaux.append(f"suiveur -{reglages['suiveur_pct']:g} %")
    if reglages.get("baisse_pct"):
        morceaux.append(f"séance -{reglages['baisse_pct']:g} %")
    return f" [alerte {', '.join(morceaux)}]" if morceaux else ""


def _ligne(p: dict) -> str:
    extras = [f"stop {p['stop']:g}"] if p.get("stop") else []
    if p.get("suiveur_pct"):
        extras.append(f"stop suiveur {p['suiveur_pct']:g} %")
    if p.get("diversifie"):
        extras.append("ETF")
    if p.get("devise", "EUR") != "EUR":
        extras.append(f"coté en {p['devise']}")
    return (f"• {p.get('nom', p['ticker'])} ({p['ticker']}), {p.get('enveloppe', '?')} : "
            f"{p.get('quantite', 0):g} à {p.get('prix_revient', 0):g} €"
            + (f" ({', '.join(extras)})" if extras else ""))


Reponse = tuple[str, str, Any]  # (chat id, texte, boutons)


def commande(journal, chat_id: str, nom: str, texte: str, admin_id: str,
             valider: Callable[[str], bool], max_utilisateurs: int = 10) -> list[Reponse]:
    """Traite un message texte reçu sur Telegram. ``valider`` dit si un ticker a
    des cours (Yahoo Finance). Renvoie les messages à envoyer. Les commandes
    qui demandent des cours (/portefeuille) sont traitées par l'appelant."""
    chat_id = str(chat_id)
    mots = texte.strip().split()
    if not mots or not mots[0].startswith("/"):
        return []
    ordre = mots[0][1:].split("@")[0].lower()
    args = mots[1:]
    u = journal.utilisateur(chat_id)

    if not est_actif(u):
        if ordre != "start":
            return [(chat_id, "Écris /start pour demander l'accès à ce bot.", None)]
        if u and u["statut"] == EN_ATTENTE:
            return [(chat_id, "Ta demande est déjà transmise : tu seras prévenu dès qu'elle est acceptée.", None)]
        if u and u["statut"] == REFUSE:
            return [(chat_id, "Ta demande d'accès a été refusée.", None)]
        if len(actifs(journal)) >= max_utilisateurs or len(journal.utilisateurs(EN_ATTENTE)) >= DEMANDES_MAX:
            return [(chat_id, "Le bot ne prend pas de nouvelle demande pour l'instant.", None)]
        nom = (nom or chat_id)[:40]
        journal.enregistrer_utilisateur({"chat_id": chat_id, "nom": nom, "statut": EN_ATTENTE})
        return [
            (chat_id, "Demande transmise à l'administrateur du bot. Bon à savoir : tes positions seront "
                      "enregistrées sur son serveur, et il peut techniquement les lire. /quitter les efface "
                      "à tout moment.", None),
            (admin_id, f"👤 {nom} demande à utiliser le bot.",
             [("✅ Accepter", f"accepter:{chat_id}"), ("❌ Refuser", f"refuser:{chat_id}")]),
        ]

    def dire(message: str, boutons=None) -> list[Reponse]:
        return [(chat_id, message, boutons)]

    if ordre in ("start", "aide", "help"):
        return dire(AIDE + (AIDE_ADMIN if u["statut"] == ADMIN else ""))

    if ordre == "positions":
        if not u["portefeuille"]:
            return dire("Aucune position. Ajoute-en avec /ajouter TICKER QUANTITÉ PRIX_DE_REVIENT PEA")
        return dire("Tes positions :\n" + "\n".join(_ligne(p) for p in u["portefeuille"]))

    if ordre == "ajouter":
        if len(args) < 3:
            return dire("Usage : /ajouter TICKER QUANTITÉ PRIX_DE_REVIENT [PEA|CTO] [nom]\n"
                        "ex. /ajouter MC.PA 3 612,40 PEA LVMH")
        ticker, quantite, prix = args[0].upper(), _nombre(args[1]), _nombre(args[2])
        if quantite is None or prix is None or quantite <= 0 or prix < 0:
            return dire("Quantité et prix de revient doivent être des nombres positifs (ex. 3 et 612,40).")
        enveloppe = args[3].upper() if len(args) > 3 and args[3].upper() in ENVELOPPES else "CTO"
        reste = args[4:] if len(args) > 3 and args[3].upper() in ENVELOPPES else args[3:]
        if not valider(ticker):
            return dire(f"Pas de cours pour {ticker} sur Yahoo Finance. Vérifie le suffixe de la place "
                        "(MC.PA pour Paris, ASML.AS pour Amsterdam, rien pour les US).")
        position = {"ticker": ticker, "nom": " ".join(reste) or annuaire.nom(ticker), "enveloppe": enveloppe,
                    "quantite": quantite, "prix_revient": prix}
        if cons.devise_de(ticker) != "EUR":
            position["devise"] = cons.devise_de(ticker)
        anciennes = [p for p in u["portefeuille"] if (p["ticker"], p.get("enveloppe")) == (ticker, enveloppe)]
        if anciennes:
            # les réglages de la ligne survivent à la mise à jour de la quantité
            for cle in ("stop", "suiveur_pct", "achat_le", "diversifie"):
                if anciennes[0].get(cle):
                    position[cle] = anciennes[0][cle]
        autres = [p for p in u["portefeuille"] if p not in anciennes]
        if len(autres) >= POSITIONS_MAX:
            return dire(f"{POSITIONS_MAX} positions au plus.")
        u["portefeuille"] = autres + [position]
        journal.enregistrer_utilisateur(u)
        return dire(("Position mise à jour" if anciennes else "Position ajoutée") + " :\n" + _ligne(position))

    if ordre == "retirer":
        if not args:
            return dire("Usage : /retirer TICKER [enveloppe]")
        ticker = args[0].upper()
        enveloppe = args[1].upper() if len(args) > 1 else None
        restantes = [p for p in u["portefeuille"]
                     if not (p["ticker"] == ticker and enveloppe in (None, p.get("enveloppe")))]
        if len(restantes) == len(u["portefeuille"]):
            return dire(f"{ticker} n'est pas dans tes positions.")
        u["portefeuille"] = restantes
        journal.enregistrer_utilisateur(u)
        return dire(f"{ticker} retiré de tes positions.")

    if ordre == "stop":
        prix = _nombre(args[1]) if len(args) > 1 else None
        if prix is None or prix < 0:
            return dire("Usage : /stop TICKER PRIX (dans la devise de cotation ; 0 pour l'enlever)")
        ticker = args[0].upper()
        if erreur := _regler(u, ticker, "stop", prix, valider):
            return dire(erreur)
        journal.enregistrer_utilisateur(u)
        return dire(f"Seuil de {ticker} : alerte sous {prix:g}." if prix else f"Seuil de {ticker} enlevé.")

    if ordre == "suiveur":
        pct = _nombre(args[1].replace("%", "")) if len(args) > 1 else None
        if pct is None or not 0 <= pct < 100:
            return dire("Usage : /suiveur TICKER POURCENT (ex. /suiveur MC.PA 12 ; 0 pour l'enlever)")
        ticker = args[0].upper()
        if erreur := _regler(u, ticker, "suiveur_pct", pct, valider):
            return dire(erreur)
        journal.enregistrer_utilisateur(u)
        if not pct:
            return dire(f"Stop suiveur de {ticker} enlevé.")
        return dire(f"Stop suiveur de {ticker} : alerte à -{pct:g} % de son plus haut des 6 derniers mois. "
                    "Le niveau du jour figure dans le résumé du matin.")

    if ordre == "baisse":
        pct = _nombre(args[1].replace("%", "").lstrip("-")) if len(args) > 1 else None
        if pct is None or not 0 <= pct < 100:
            return dire("Usage : /baisse TICKER POURCENT (ex. /baisse WLD.PA 1 ; 0 pour l'enlever)")
        ticker = args[0].upper()
        if erreur := _regler(u, ticker, "baisse_pct", pct, valider):
            return dire(erreur)
        journal.enregistrer_utilisateur(u)
        if not pct:
            return dire(f"Alerte de baisse sur {ticker} enlevée.")
        return dire(f"Alerte dès que {ticker} perd {pct:g} % sur la séance, puis à chaque {pct:g} % de plus. "
                    "Le cours est relevé à chaque contrôle de la veille.")

    if ordre == "etf":
        ticker = args[0].upper() if args else ""
        visees = [p for p in u["portefeuille"] if p["ticker"] == ticker]
        if not visees:
            return dire("Usage : /etf TICKER (une de tes positions)")
        etf = not visees[0].get("diversifie")
        for p in visees:
            if etf:
                p["diversifie"] = True
            else:
                p.pop("diversifie", None)
        journal.enregistrer_utilisateur(u)
        return dire(f"{ticker} marqué comme ETF : il échappe au plafond de poids par titre." if etf
                    else f"{ticker} n'est plus marqué comme ETF.")

    if ordre == "suivre":
        if not args:
            return dire("Usage : /suivre TICKER [TICKER…]")
        ajoutes, inconnus = [], []
        for ticker in (a.upper() for a in args):
            if ticker in u["suivis"]:
                continue
            if len(u["suivis"]) >= SUIVIS_MAX:
                inconnus.append(f"{ticker} (limite de {SUIVIS_MAX} atteinte)")
            elif valider(ticker):
                ajoutes.append(ticker)
                u["suivis"].append(ticker)
            else:
                inconnus.append(ticker)
        journal.enregistrer_utilisateur(u)
        message = f"Surveillées en plus : {', '.join(ajoutes)}." if ajoutes else "Rien de nouveau à surveiller."
        if inconnus:
            message += f"\nNon ajoutées : {', '.join(inconnus)}. Vérifie le suffixe de la place (MC.PA, ASML.AS…)."
        return dire(message)

    if ordre == "oublier":
        tickers = {a.upper() for a in args}
        if not tickers & set(u["suivis"]):
            return dire("Usage : /oublier TICKER, pour une valeur de /liste")
        u["suivis"] = [t for t in u["suivis"] if t not in tickers]
        u["alertes_suivis"] = {t: r for t, r in (u.get("alertes_suivis") or {}).items() if t not in tickers}
        journal.enregistrer_utilisateur(u)
        return dire(f"Tu ne surveilles plus {', '.join(sorted(tickers))}.")

    if ordre == "liste":
        if not u["suivis"]:
            return dire("Aucune valeur surveillée en dehors de tes positions. Ajoute-en avec /suivre TICKER")
        reglages = u.get("alertes_suivis") or {}
        return dire("Valeurs surveillées : " + ", ".join(
                        (f"{annuaire.nom(t)} ({t})" if annuaire.nom(t) != t else t) + _seuils_suivi(reglages.get(t, {}))
                        for t in u["suivis"]) +
                    "\nTu reçois sur elles les alertes de chute et les opportunités, comme sur tes positions.")

    if ordre == "chercher":
        if not args:
            return dire("Usage : /chercher MOTS, par exemple /chercher defense france ou /chercher luxe")
        trouvees = annuaire.chercher(" ".join(args))
        if not trouvees:
            return dire("Rien trouvé. Essaie un nom (sanofi), un secteur (defense, banques, luxe, sante, "
                        "semi-conducteurs, energie…) et un pays (france, allemagne, usa, europe).")
        lignes = [f"• {s.ticker} : {annuaire.court(s.nom)}, {s.industrie or s.secteur or '?'}, {s.pays}"
                  + (f", {s.taille.lower()}" if s.taille else "") for s in trouvees]
        boutons = [[(f"➕ {s.ticker}", f"suivre:{s.ticker}") for s in trouvees[i:i + 3]]
                   for i in range(0, len(trouvees), 3)]
        return dire("Les plus grandes sociétés trouvées (cotées en euros ou en dollars) :\n" + "\n".join(lignes)
                    + "\n\nTouche ➕ pour la surveiller, ou /ajouter si tu la détiens.", boutons)

    if ordre == "poche":
        montant = _nombre(args[0]) if args else None
        if montant is None or montant < 0:
            return dire("Usage : /poche MONTANT (en euros ; 0 pour reprendre la valeur par défaut)")
        u["poche_eur"] = montant or None
        journal.enregistrer_utilisateur(u)
        return dire(f"Poche des conseils d'achat : {montant:g} €." if montant
                    else "Poche des conseils d'achat : valeur par défaut.")

    if ordre == "quitter":
        if u["statut"] == ADMIN:
            return dire("L'administrateur ne peut pas quitter son propre bot.")
        return dire("Effacer tes positions, ta liste et l'historique de tes conseils ? C'est définitif.",
                    [("🗑️ Tout effacer", f"quitter:{chat_id}"), ("Annuler", "quitter:non")])

    if ordre == "utilisateurs" and u["statut"] == ADMIN:
        lignes = [f"• {v['nom']} : {v['statut']}, {len(v['portefeuille'])} positions, "
                  f"{len(v['suivis'])} surveillées" for v in journal.utilisateurs()]
        return dire("Utilisateurs :\n" + "\n".join(lignes))

    return dire("Commande inconnue. /aide pour la liste.")


def decider_acces(journal, chat_id: str, accepter: bool) -> Reponse | None:
    """Réponse de l'administrateur à une demande d'accès ; message pour le demandeur."""
    u = journal.utilisateur(chat_id)
    if u is None or u["statut"] != EN_ATTENTE:
        return None
    u["statut"] = ACTIF if accepter else REFUSE
    journal.enregistrer_utilisateur(u)
    if accepter:
        return chat_id, "✅ Accès accepté. Ajoute tes positions et tes valeurs à surveiller :\n\n" + AIDE, None
    return chat_id, "Ta demande d'accès a été refusée.", None
