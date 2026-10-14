"""Envoi des messages : Telegram, ntfy, et toujours la console."""

from __future__ import annotations

import logging
import os
from typing import Any

import requests

log = logging.getLogger(__name__)

LIMITE_TELEGRAM = 4000  # Telegram refuse au-delà de 4096 caractères


class Notifieur:
    def __init__(self, config: dict[str, Any]):
        tg = config.get("telegram", {})
        self.token = os.environ.get(tg.get("token_env", ""), "")
        self.chat_id = os.environ.get(tg.get("chat_id_env", ""), "")
        self.ntfy = os.environ.get(config.get("ntfy", {}).get("topic_env", ""), "")

    def envoyer(self, texte: str, titre: str = "Boursicoteur", urgent: bool = False,
                boutons: list[tuple[str, str]] | None = None) -> None:
        """``boutons`` : (libellé, donnée renvoyée au clic), affichés sous le message Telegram."""
        print(f"\n=== {titre} ===\n{texte}\n")
        if self.token and self.chat_id:
            morceaux = decouper(f"{titre}\n{texte}", LIMITE_TELEGRAM)
            for i, morceau in enumerate(morceaux):
                corps = {"chat_id": self.chat_id, "text": morceau, "disable_web_page_preview": True}
                if boutons and i == len(morceaux) - 1:
                    corps["reply_markup"] = {"inline_keyboard": [
                        [{"text": libelle, "callback_data": donnee} for libelle, donnee in boutons]
                    ]}
                self._poster(self._api("sendMessage"), json=corps)
        if self.ntfy:
            url = self.ntfy if self.ntfy.startswith("http") else f"https://ntfy.sh/{self.ntfy}"
            self._poster(
                url,
                data=texte.encode("utf-8"),
                headers={"Title": titre.encode("utf-8"), "Priority": "high" if urgent else "default"},
            )

    # --- boutons Telegram ------------------------------------------------------
    def clics(self, offset: int | None, attente: int) -> tuple[list[dict[str, Any]], int | None]:
        """Attend jusqu'à ``attente`` secondes les clics sur les boutons.
        Seuls les clics venant de ton chat sont renvoyés : un inconnu qui
        trouverait le bot ne peut rien déclencher."""
        if not (self.token and self.chat_id):
            return [], offset
        try:
            reponse = requests.get(
                self._api("getUpdates"),
                params={"offset": offset, "timeout": attente,
                        "allowed_updates": '["callback_query"]'},
                timeout=attente + 15,
            )
            reponse.raise_for_status()
            mises_a_jour = reponse.json().get("result", [])
        except Exception as exc:
            log.warning("Lecture des clics impossible : %s", type(exc).__name__)
            return [], offset
        clics = []
        for maj in mises_a_jour:
            offset = maj["update_id"] + 1
            clic = maj.get("callback_query")
            if clic and str(clic.get("message", {}).get("chat", {}).get("id")) == str(self.chat_id):
                clics.append(clic)
        return clics, offset

    def accuser_clic(self, clic: dict[str, Any], confirmation: str) -> None:
        """Retire les boutons du message et confirme ce qui a été noté."""
        self._poster(self._api("answerCallbackQuery"),
                     json={"callback_query_id": clic["id"], "text": confirmation})
        message = clic.get("message", {})
        if message:
            self._poster(self._api("editMessageReplyMarkup"), json={
                "chat_id": self.chat_id, "message_id": message["message_id"],
                "reply_markup": {"inline_keyboard": []},
            })
        self._poster(self._api("sendMessage"), json={"chat_id": self.chat_id, "text": confirmation})

    def _api(self, methode: str) -> str:
        return f"https://api.telegram.org/bot{self.token}/{methode}"

    @staticmethod
    def _poster(url: str, **kwargs: Any) -> None:
        try:
            requests.post(url, timeout=20, **kwargs).raise_for_status()
        except Exception as exc:
            # on ne journalise pas l'URL : elle contient le jeton Telegram
            log.warning("Notification non envoyée : %s", type(exc).__name__)


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
