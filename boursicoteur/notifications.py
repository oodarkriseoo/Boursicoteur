"""Envoi des messages : Telegram, ntfy, et toujours la console."""

from __future__ import annotations

import html
import logging
import os
import re
from typing import Any

import requests

log = logging.getLogger(__name__)

LIMITE_TELEGRAM = 4000  # Telegram refuse au-delà de 4096 caractères

# Tickers avec suffixe de place (SAN.PA, ASML.AS, NOV.DE) : sans précaution,
# Telegram les prend pour des adresses web (.pa, .as et .de sont des domaines).
TICKER_A_SUFFIXE = re.compile(r"(?<![\w/.])([A-Z0-9]{1,8}\.[A-Z]{1,2})(?![\w.])")


def pour_telegram(texte: str) -> str:
    """Texte au format HTML de Telegram, tickers à suffixe affichés en code (non cliquables)."""
    return TICKER_A_SUFFIXE.sub(r"<code>\1</code>", html.escape(texte, quote=False))


class Notifieur:
    def __init__(self, config: dict[str, Any]):
        tg = config.get("telegram", {})
        self.token = os.environ.get(tg.get("token_env", ""), "")
        self.chat_id = os.environ.get(tg.get("chat_id_env", ""), "")
        self.ntfy = os.environ.get(config.get("ntfy", {}).get("topic_env", ""), "")

    def envoyer(self, texte: str, titre: str = "Boursicoteur", urgent: bool = False,
                boutons: list | None = None, chat_id: str | None = None) -> None:
        """``boutons`` : (libellé, donnée renvoyée au clic) affichés sous le message
        Telegram, sur une ligne ; ou une liste de lignes de tels couples.
        ``chat_id`` : conversation d'un autre utilisateur ; par défaut celle de
        l'administrateur, seul à recevoir aussi les messages ntfy."""
        pour_admin = chat_id is None or str(chat_id) == str(self.chat_id)
        chat_id = self.chat_id if chat_id is None else str(chat_id)
        print(f"\n=== {titre}{'' if pour_admin else f' (pour {chat_id})'} ===\n{texte}\n")
        if self.token and chat_id:
            morceaux = decouper(f"{titre}\n{texte}" if titre else texte, LIMITE_TELEGRAM)
            for i, morceau in enumerate(morceaux):
                corps = {"chat_id": chat_id, "text": pour_telegram(morceau), "parse_mode": "HTML",
                         "disable_web_page_preview": True}
                if boutons and i == len(morceaux) - 1:
                    lignes = [boutons] if isinstance(boutons[0], tuple) else boutons
                    corps["reply_markup"] = {"inline_keyboard": [
                        [{"text": libelle, "callback_data": donnee} for libelle, donnee in ligne]
                        for ligne in lignes
                    ]}
                self._poster(self._api("sendMessage"), json=corps)
        if self.ntfy and pour_admin:
            url = self.ntfy if self.ntfy.startswith("http") else f"https://ntfy.sh/{self.ntfy}"
            self._poster(
                url,
                data=texte.encode("utf-8"),
                headers={"Title": titre.encode("utf-8"), "Priority": "high" if urgent else "default"},
            )

    # --- boutons Telegram ------------------------------------------------------
    def mises_a_jour(self, offset: int | None, attente: int
                     ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int | None]:
        """Attend jusqu'à ``attente`` secondes les clics sur les boutons et les
        messages texte (commandes). Renvoie (clics, messages, offset). Rien
        n'est filtré ici : l'appelant vérifie que l'expéditeur est un
        utilisateur accepté avant d'agir."""
        if not (self.token and self.chat_id):
            return [], [], offset
        try:
            reponse = requests.get(
                self._api("getUpdates"),
                params={"offset": offset, "timeout": attente,
                        "allowed_updates": '["callback_query", "message"]'},
                timeout=attente + 15,
            )
            reponse.raise_for_status()
            mises_a_jour = reponse.json().get("result", [])
        except Exception as exc:
            log.warning("Lecture des clics impossible : %s", type(exc).__name__)
            return [], [], offset
        clics, messages = [], []
        for maj in mises_a_jour:
            offset = maj["update_id"] + 1
            if maj.get("callback_query"):
                clics.append(maj["callback_query"])
            message = maj.get("message") or {}
            # conversations privées seulement : dans un groupe, tout le monde lirait les portefeuilles
            if message.get("text") and message.get("chat", {}).get("type", "private") == "private":
                messages.append(message)
        return clics, messages, offset

    def declarer_commandes(self, commandes: list[tuple[str, str]]) -> None:
        """Menu « / » de Telegram : (commande, description)."""
        if self.token:
            self._poster(self._api("setMyCommands"), json={
                "commands": [{"command": c, "description": d} for c, d in commandes]})

    def accuser_clic(self, clic: dict[str, Any], confirmation: str, message: bool = True,
                     retirer: bool = True) -> None:
        """Confirme le clic ; retire les boutons du message (``retirer``) et
        envoie la confirmation en message (``message``)."""
        self._poster(self._api("answerCallbackQuery"),
                     json={"callback_query_id": clic["id"], "text": confirmation})
        origine = clic.get("message", {})
        chat_id = chat_du_clic(clic) or self.chat_id
        if origine and retirer:
            self._poster(self._api("editMessageReplyMarkup"), json={
                "chat_id": chat_id, "message_id": origine["message_id"],
                "reply_markup": {"inline_keyboard": []},
            })
        if message:
            self._poster(self._api("sendMessage"), json={
                "chat_id": chat_id, "text": pour_telegram(confirmation), "parse_mode": "HTML"})

    def _api(self, methode: str) -> str:
        return f"https://api.telegram.org/bot{self.token}/{methode}"

    @staticmethod
    def _poster(url: str, **kwargs: Any) -> None:
        try:
            requests.post(url, timeout=20, **kwargs).raise_for_status()
        except Exception as exc:
            # on ne journalise pas l'URL : elle contient le jeton Telegram
            log.warning("Notification non envoyée : %s", type(exc).__name__)


def chat_du_clic(clic: dict[str, Any]) -> str:
    return str(clic.get("message", {}).get("chat", {}).get("id", ""))


def decouper(texte: str, limite: int) -> list[str]:
    morceaux, courant = [], ""
    for ligne in texte.splitlines(keepends=True):
        while len(ligne) > limite:
            if courant:
                morceaux.append(courant)
                courant = ""
            morceaux.append(ligne[:limite])
            ligne = ligne[limite:]
        if len(courant) + len(ligne) > limite:
            morceaux.append(courant)
            courant = ""
        courant += ligne
    if courant:
        morceaux.append(courant)
    return morceaux
