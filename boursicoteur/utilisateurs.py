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
/suivre TICKER… : surveiller des valeurs sans les détenir
/oublier TICKER : ne plus surveiller
/liste : valeurs surveillées
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


def destinataires(utilisateurs: list[dict], ticker: str, commun: set[str]) -> list[dict]:
    """Qui prévenir pour une valeur : tout le monde si elle fait partie des thèmes
    communs, sinon ceux qui la détiennent ou la surveillent."""
    if ticker in commun:
        return utilisateurs
    return [u for u in utilisateurs if ticker in tickers_de(u)]


def _nombre(texte: str) -> float | None:
    try:
        return float(texte.replace(",", ".").replace("€", ""))
    except ValueError:
        return None


def _ligne(p: dict) -> str:
    extras = [f"stop {p['stop']:g}"] if p.get("stop") else []
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
        position = {"ticker": ticker, "nom": " ".join(reste) or ticker, "enveloppe": enveloppe,
                    "quantite": quantite, "prix_revient": prix}
        if cons.devise_de(ticker) != "EUR":
            position["devise"] = cons.devise_de(ticker)
        anciennes = [p for p in u["portefeuille"] if (p["ticker"], p.get("enveloppe")) == (ticker, enveloppe)]
        if anciennes and anciennes[0].get("stop"):
            position["stop"] = anciennes[0]["stop"]
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
        visees = [p for p in u["portefeuille"] if p["ticker"] == ticker]
        if not visees:
            return dire(f"{ticker} n'est pas dans tes positions.")
        for p in visees:
            if prix:
                p["stop"] = prix
            else:
                p.pop("stop", None)
        journal.enregistrer_utilisateur(u)
        return dire(f"Seuil de sortie de {ticker} : {prix:g}." if prix else f"Seuil de sortie de {ticker} enlevé.")

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
        journal.enregistrer_utilisateur(u)
        return dire(f"Tu ne surveilles plus {', '.join(sorted(tickers))}.")

    if ordre == "liste":
        if not u["suivis"]:
            return dire("Aucune valeur surveillée en dehors de tes positions. Ajoute-en avec /suivre TICKER")
        return dire("Valeurs surveillées : " + ", ".join(u["suivis"]) +
                    "\nTu reçois sur elles les alertes de chute et les opportunités, comme sur tes positions.")

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
