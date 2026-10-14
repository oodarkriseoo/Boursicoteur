"""Passage d'ordres chez Interactive Brokers, uniquement après ta confirmation.

Le bot ne décide jamais seul : un ordre part seulement quand tu as cliqué
« Acheter via IBKR » puis « Confirmer » dans Telegram. L'ordre est un
« bracket » : achat à cours limité, avec la vente à l'objectif et le stop
déjà attachés chez le courtier, qui les exécute même si le serveur s'arrête.

Garde-fous, vérifiés avant chaque ordre :
- un plafond par ordre et un plafond par jour, en euros ;
- tant que ``compte_demo`` est vrai, refus de tout compte qui n'est pas un
  compte de démonstration (les comptes de démo IBKR commencent par « D ») ;
- seules les places connues sont acceptées (États-Unis, Paris, Amsterdam, Xetra, Milan).

Il faut IB Gateway (logiciel d'IBKR) qui tourne sur le serveur ; voir le README.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

log = logging.getLogger(__name__)

# suffixe Yahoo → (place IBKR, devise)
PLACES = {"PA": ("SBF", "EUR"), "AS": ("AEB", "EUR"), "DE": ("IBIS", "EUR"), "MI": ("BVME", "EUR")}


def contrat_ibkr(ticker: str) -> tuple[str, str, str] | None:
    """(symbole, place, devise) pour IBKR, ou None si la valeur n'est pas prise en charge."""
    if "-" in ticker or ticker.startswith("^") or "=" in ticker:
        return None  # crypto, indices, devises
    if "." in ticker:
        symbole, suffixe = ticker.rsplit(".", 1)
        if suffixe not in PLACES:
            return None
        return (symbole, *PLACES[suffixe])
    return ticker, "SMART", "USD"


@dataclass
class Resultat:
    ok: bool
    message: str


class Courtier:
    def __init__(self, reglages: dict[str, Any], fabrique_ib: Callable[[], Any] | None = None,
                 fabrique_action: Callable[[str, str, str], Any] | None = None):
        """Les fabriques permettent de tester sans ib_async ni IB Gateway."""
        self.reglages = reglages
        self._fabrique_ib = fabrique_ib
        self._fabrique_action = fabrique_action

    @property
    def actif(self) -> bool:
        return self.reglages.get("type") == "ibkr"

    def verifier(self, ticker: str, quantite: float, montant_eur: float, engage_jour_eur: float) -> str | None:
        """Raison du refus, ou None si l'ordre respecte les garde-fous."""
        if not self.actif:
            return "Aucun courtier branché (section « courtier » de la configuration)."
        if contrat_ibkr(ticker) is None:
            return f"{ticker} n'est pas négociable via le bot (place non prise en charge)."
        if quantite < 1 or quantite != int(quantite):
            return "La quantité doit être un nombre entier d'actions, au moins 1."
        if montant_eur > self.reglages["plafond_ordre_eur"]:
            return (f"Ordre de {montant_eur:.0f} € refusé : plafond par ordre "
                    f"{self.reglages['plafond_ordre_eur']:.0f} €.")
        if engage_jour_eur + montant_eur > self.reglages["plafond_jour_eur"]:
            return (f"Ordre refusé : {engage_jour_eur:.0f} € déjà engagés aujourd'hui, "
                    f"plafond {self.reglages['plafond_jour_eur']:.0f} €.")
        return None

    def acheter(self, ticker: str, quantite: float, prix_max: float, objectif: float, stop: float,
                montant_eur: float, engage_jour_eur: float) -> Resultat:
        refus = self.verifier(ticker, quantite, montant_eur, engage_jour_eur)
        if refus:
            return Resultat(False, refus)
        symbole, place, devise = contrat_ibkr(ticker)
        ib = self._ib()
        try:
            ib.connect(self.reglages["hote"], int(self.reglages["port"]),
                       clientId=int(self.reglages["client_id"]), timeout=15)
            comptes = list(ib.managedAccounts())
            if self.reglages.get("compte_demo", True) and not all(c.startswith("D") for c in comptes):
                return Resultat(False, "Compte réel détecté alors que compte_demo est activé : aucun ordre passé.")
            contrat = self._action(symbole, place, devise)
            if not ib.qualifyContracts(contrat):
                return Resultat(False, f"IBKR ne reconnaît pas {symbole} sur {place}.")
            bracket = ib.bracketOrder("BUY", int(quantite), round(prix_max, 2), round(objectif, 2),
                                      round(stop, 2), tif="GTC")
            bracket.parent.tif = "DAY"  # l'achat expire ce soir s'il n'est pas exécuté
            ordres = [ib.placeOrder(contrat, o) for o in bracket]
            ib.sleep(2)
            statut = ordres[0].orderStatus.status if ordres else "inconnu"
            compte = "démo" if all(c.startswith("D") for c in comptes) else "réel"
            return Resultat(True, (
                f"🛒 Ordre envoyé à IBKR (compte {compte}) : achat de {int(quantite)} {symbole} "
                f"à {prix_max:.2f} {devise} maximum, stop {stop:.2f}, objectif {objectif:.2f}. "
                f"Statut : {statut}. L'achat expire ce soir s'il n'est pas exécuté ; "
                "le stop et l'objectif restent actifs chez IBKR."
            ))
        except Exception as exc:
            log.exception("Ordre IBKR en échec")
            return Resultat(False, f"Ordre non passé : {type(exc).__name__} {exc}. IB Gateway tourne-t-il ?")
        finally:
            try:
                ib.disconnect()
            except Exception:
                pass

    def _action(self, symbole: str, place: str, devise: str):
        if self._fabrique_action:
            return self._fabrique_action(symbole, place, devise)
        from ib_async import Stock

        return Stock(symbole, place, devise)

    def _ib(self):
        if self._fabrique_ib:
            return self._fabrique_ib()
        from ib_async import IB

        return IB()
