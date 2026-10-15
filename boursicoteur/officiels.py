"""Sources officielles, gratuites et sans clé, sur les sociétés suivies.

- AMF (API info-financière, données de la DILA) : communiqués réglementés des
  sociétés cotées en France, quelques minutes après leur dépôt.
- EMA : avis et autorisations de médicaments dans l'Union (fichier JSON mis à
  jour deux fois par jour).
- FDA (openFDA) : nouvelles autorisations et nouvelles indications aux États-Unis.
- ClinicalTrials.gov : essais de phase 3 terminés, arrêtés ou avec résultats.

Chaque source renvoie des faits rattachés à un ticker grâce à l'annuaire (ISIN
pour l'AMF, nom du laboratoire pour la santé). Le premier passage note tout
comme déjà vu sans alerter : seules les nouveautés partent ensuite.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable

import requests

from . import annuaire

log = logging.getLogger(__name__)

URL_AMF = "https://www.info-financiere.gouv.fr/api/explore/v2.1/catalog/datasets/flux-amf-new-prod/records"
URL_EMA = "https://www.ema.europa.eu/en/documents/report/medicines-output-medicines_json-report_en.json"
URL_FDA = "https://api.fda.gov/drug/drugsfda.json"
URL_ESSAIS = "https://clinicaltrials.gov/api/v2/studies"
AGENT = {"User-Agent": "Boursicoteur (veille personnelle)"}

# publications répétitives sans portée sur le cours
BRUIT_AMF = ("droits de vote", "actions propres", "contrat de liquidit", "honoraires", "descriptif du programme")
STATUTS_ESSAI = {"COMPLETED": "terminé", "TERMINATED": "arrêté", "SUSPENDED": "suspendu",
                 "WITHDRAWN": "retiré"}


@dataclass
class Fait:
    source: str  # AMF, EMA, FDA, Essais
    ticker: str
    titre: str
    lien: str
    categorie: str  # catégorie de catalyseur, mesurée comme les autres news
    cle: str  # identifiant unique : un changement de statut donne une nouvelle clé


def _get(url: str, params: dict | None = None, timeout: int = 30) -> Any:
    reponse = requests.get(url, params=params, headers=AGENT, timeout=timeout)
    reponse.raise_for_status()
    return reponse.json()


def _champ(enregistrement: dict, *indices: str) -> str:
    """Premier champ texte dont le nom contient un des indices (les noms exacts
    de certains jeux de données ne sont pas documentés)."""
    for indice in indices:
        for nom, valeur in enregistrement.items():
            if indice in nom.lower() and isinstance(valeur, str) and valeur.strip():
                return valeur.strip()
    return ""


def _lien(enregistrement: dict) -> str:
    liens = [v for v in enregistrement.values() if isinstance(v, str) and v.startswith("http")]
    return next((l for l in liens if ".pdf" in l.lower()), liens[0] if liens else "")


# --- AMF -------------------------------------------------------------------------

def amf(isins: dict[str, str], depuis: date, get: Callable = _get) -> list[Fait]:
    """Publications réglementées depuis ``depuis`` pour ces ISIN (ISIN → ticker)."""
    if not isins:
        return []
    filtre = " or ".join(f'identificationsociete_iso_cd_isi="{i}"' for i in isins)
    donnees = get(URL_AMF, {"where": f"uin_dat_amf >= date'{depuis.isoformat()}' and ({filtre})",
                            "order_by": "uin_dat_amf desc", "limit": 100})
    faits = []
    for e in donnees.get("results", []):
        ticker = isins.get(e.get("identificationsociete_iso_cd_isi"))
        langue = str(e.get("language") or "").lower()
        if not ticker or langue.startswith("en"):
            continue  # les sociétés bilingues déposent aussi la version anglaise
        titre = _champ(e, "titre", "title", "libelle", "objet", "tit") or "Nouvelle publication réglementée"
        if any(b in titre.lower() for b in BRUIT_AMF):
            continue
        identifiant = e.get("uin_idt_uin") or e.get("id") or _lien(e) or titre
        faits.append(Fait("AMF", ticker, titre, _lien(e) or "https://www.info-financiere.gouv.fr/",
                          "communiqué officiel", f"amf:{identifiant}"))
    if donnees.get("results") and not faits:
        log.info("AMF : champs reçus %s", sorted(donnees["results"][0]))
    return faits


# --- santé -----------------------------------------------------------------------

def laboratoire(nom: str, laboratoires: dict[str, str]) -> str | None:
    """Ticker du laboratoire dont la clé figure dans ``nom`` (titulaire, promoteur)."""
    texte = annuaire.cle(nom) if nom else ""
    return next((t for cle, t in laboratoires.items() if cle and cle in texte), None)


def _date(texte: str) -> date | None:
    for format_ in ("%d/%m/%Y", "%Y-%m-%d", "%Y%m%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(str(texte)[:10], format_).date()
        except ValueError:
            continue
    return None


def ema(laboratoires: dict[str, str], aujourd_hui: date | None = None, jours: int = 30,
        get: Callable = _get) -> list[Fait]:
    """Avis du comité des médicaments et décisions de la Commission de moins de ``jours``."""
    aujourd_hui = aujourd_hui or date.today()
    donnees = get(URL_EMA, timeout=120)
    if isinstance(donnees, dict):
        donnees = next((v for v in donnees.values() if isinstance(v, list)), [])
    faits = []
    for m in donnees:
        if not isinstance(m, dict) or str(m.get("category", "Human")).lower().startswith("vet"):
            continue
        ticker = laboratoire(m.get("marketing_authorisation_developer_applicant_holder", ""), laboratoires)
        if not ticker:
            continue
        avis = _date(m.get("opinion_adopted_date", ""))
        decision = _date(m.get("european_commission_decision_date", ""))
        recentes = [d for d in (avis, decision) if d and (aujourd_hui - d).days <= jours]
        if not recentes:
            continue
        nom = m.get("name_of_medicine", "?")
        substance = m.get("active_substance") or m.get("international_non_proprietary_name_common_name") or ""
        etat = (f"autorisé par la Commission le {decision:%d/%m/%Y}" if decision in recentes
                else f"avis {str(m.get('opinion_status') or '').lower() or 'rendu'} du comité le {avis:%d/%m/%Y}")
        indication = (m.get("therapeutic_indication") or "")[:200]
        titre = f"EMA : {nom}{f' ({substance})' if substance else ''}, {etat}." + (f" {indication}" if indication else "")
        faits.append(Fait("EMA", ticker, titre, m.get("medicine_url") or "https://www.ema.europa.eu/",
                          "autorisation médicament",
                          f"ema:{m.get('ema_product_number', nom)}:{m.get('opinion_status')}:{decision}"))
    return faits


def fda(laboratoires: dict[str, str], aujourd_hui: date | None = None, jours: int = 14,
        get: Callable = _get) -> list[Fait]:
    """Nouvelles autorisations (ORIG) et nouvelles indications (SUPPL d'efficacité)."""
    aujourd_hui = aujourd_hui or date.today()
    debut = aujourd_hui - timedelta(days=jours)
    try:
        donnees = get(URL_FDA, {"search": f"submissions.submission_status_date:[{debut:%Y%m%d} TO {aujourd_hui:%Y%m%d}]",
                                "limit": 100})
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            return []  # openFDA répond 404 quand rien ne correspond
        raise
    faits = []
    for r in donnees.get("results", []):
        ticker = laboratoire(r.get("sponsor_name", ""), laboratoires)
        if not ticker:
            continue
        marque = next((p.get("brand_name") for p in r.get("products", []) if p.get("brand_name")), "?")
        numero = r.get("application_number", "")
        for s in r.get("submissions", []):
            jour = _date(s.get("submission_status_date", ""))
            nouvelle = s.get("submission_type") == "ORIG"
            indication = s.get("submission_type") == "SUPPL" and "EFFICACY" in str(s.get("submission_class_code", ""))
            if s.get("submission_status") != "AP" or not jour or jour < debut or not (nouvelle or indication):
                continue
            quoi = "nouveau médicament autorisé" if nouvelle else "nouvelle indication autorisée"
            faits.append(Fait("FDA", ticker, f"FDA : {marque}, {quoi} le {jour:%d/%m/%Y}.",
                              "https://www.accessdata.fda.gov/scripts/cder/daf/index.cfm?event=overview.process"
                              f"&ApplNo={''.join(c for c in numero if c.isdigit())}",
                              "autorisation médicament", f"fda:{numero}:{s.get('submission_number')}"))
    return faits


def essais(laboratoires: dict[str, str], aujourd_hui: date | None = None, jours: int = 14,
           get: Callable = _get) -> list[Fait]:
    """Essais de phase 3 mis à jour récemment : terminés, arrêtés, suspendus ou avec résultats."""
    aujourd_hui = aujourd_hui or date.today()
    debut = aujourd_hui - timedelta(days=jours)
    faits = []
    for cle, ticker in laboratoires.items():
        donnees = get(URL_ESSAIS, {"query.spons": cle,
                                   "filter.advanced": f"AREA[LastUpdatePostDate]RANGE[{debut.isoformat()},MAX] "
                                                      "AND AREA[Phase]PHASE3",
                                   "pageSize": 50})
        for etude in donnees.get("studies", []):
            protocole = etude.get("protocolSection", {})
            ident = protocole.get("identificationModule", {})
            statut = protocole.get("statusModule", {}).get("overallStatus", "")
            promoteur = protocole.get("sponsorCollaboratorsModule", {}).get("leadSponsor", {}).get("name", "")
            resultats = bool(etude.get("hasResults"))
            if laboratoire(promoteur, {cle: ticker}) != ticker or not (statut in STATUTS_ESSAI or resultats):
                continue
            nct = ident.get("nctId", "")
            etat = "résultats publiés" if resultats else f"essai {STATUTS_ESSAI[statut]}"
            faits.append(Fait("Essais", ticker, f"Phase 3 : {ident.get('briefTitle', nct)} ({etat}).",
                              f"https://clinicaltrials.gov/study/{nct}", "résultat d'essai",
                              f"essai:{nct}:{statut}:{resultats}"))
    return faits


def formater(fait: Fait) -> str:
    icone = {"AMF": "🏛️", "EMA": "💊", "FDA": "💊", "Essais": "🧪"}.get(fait.source, "📌")
    nom = annuaire.nom(fait.ticker)
    societe = f"{nom} ({fait.ticker})" if nom != fait.ticker else fait.ticker
    return (f"{icone} {fait.source} · {societe}\n{fait.titre}\n{fait.lien}\n"
            "Source officielle : lis le document avant d'agir, le marché a pu déjà réagir.")
