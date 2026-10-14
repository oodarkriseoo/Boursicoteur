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

    def envoyer(self, texte: str, titre: str = "Boursicoteur", urgent: bool = False) -> None:
        print(f"\n=== {titre} ===\n{texte}\n")
        if self.token and self.chat_id:
            for morceau in decouper(f"*{titre}*\n{texte}", LIMITE_TELEGRAM):
                self._poster(
                    f"https://api.telegram.org/bot{self.token}/sendMessage",
                    json={"chat_id": self.chat_id, "text": morceau, "disable_web_page_preview": True},
                )
        if self.ntfy:
            url = self.ntfy if self.ntfy.startswith("http") else f"https://ntfy.sh/{self.ntfy}"
            self._poster(
                url,
                data=texte.encode("utf-8"),
                headers={"Title": titre.encode("utf-8"), "Priority": "high" if urgent else "default"},
            )

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
