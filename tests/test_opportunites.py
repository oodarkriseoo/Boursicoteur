import json

import numpy as np

from boursicoteur import opportunites as opp
from boursicoteur.config import DEFAUTS
from boursicoteur.sources.actus import Actu
from conftest import serie
from test_hypotheses import FauxClient

INTEL = Actu("Intel to manufacture chips for Nvidia in foundry deal", "http://x/intc", "Yahoo", ticker="INTC")


def decollage():
    """Cours plat puis qui décolle avec des volumes forts : le schéma recherché."""
    cours = list(np.linspace(20, 22, 60)) + [23, 24, 25, 26.5]
    volumes = [1_000_000] * 63 + [4_000_000]
    return serie(cours, volumes)


def test_catalyseurs_par_mots_cles():
    noms = [n for n, _ in opp.catalyseurs_de(INTEL)]
    assert "contrat" in noms
    assert opp.catalyseurs_de(Actu("Top 10 stocks to watch", "l", "s")) == []
    assert ("négatif", -25) in opp.catalyseurs_de(Actu("Analyst downgrade hits AMD", "l", "s"))


def test_catalyseur_frais_et_marche_qui_confirme_score_haut(baisse):
    actus = [INTEL, Actu("Weak chip faces probe", "http://x/b", "s", ticker="FAIBLE")]
    scores = opp.scorer(["INTC", "FAIBLE", "ABSENT"], {"INTC": decollage(), "FAIBLE": baisse},
                        actus, nouvelles={"http://x/intc"})
    intc, faible = scores
    assert intc.ticker == "INTC" and intc.nouveau
    assert intc.score >= 60
    assert faible.score == 0
    assert "volume 4.0× la moyenne" in intc.signaux


def test_sans_actu_le_score_reste_modeste():
    (o,) = opp.scorer(["INTC"], {"INTC": decollage()}, [], set())
    assert o.score < DEFAUTS["opportunites"]["seuil_alerte"]
    assert not o.nouveau


def test_claude_remplace_la_note_par_mots_cles():
    (o,) = opp.scorer(["INTC"], {"INTC": decollage()}, [INTEL], {"http://x/intc"})
    reponse = {"evaluations": [{"ticker": "INTC", "score_catalyseur": 5,
                                "lecture": "Simple rumeur.", "risques": "Démenti possible."}]}
    client = FauxClient(texte=json.dumps(reponse))
    assert opp.affiner_avec_claude([o], {}, "claude-sonnet-5-5", client)
    assert client.appels[0]["model"] == "claude-sonnet-5-5"
    assert o.points["catalyseur"] == 5
    texte = opp.formater(o)
    assert "Simple rumeur." in texte and "http://x/intc" in texte
