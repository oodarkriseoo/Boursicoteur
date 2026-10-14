from datetime import datetime, timedelta

import pytest

from boursicoteur import opportunites as opp
from boursicoteur.journal import Journal
from test_opportunites import INTEL, decollage

T0 = datetime(2026, 9, 1, 10)


def test_effet_mesure_a_chaque_horizon():
    j = Journal(":memory:")
    assert j.noter_catalyseur("l1", "A", ["contrat", "partenariat"], "t", 100, 50, le=T0)
    assert not j.noter_catalyseur("l1", "A", ["contrat"], "t", 100, 50, le=T0)  # déjà noté

    assert j.mesurer_catalyseurs({"A": 104}, 51, maintenant=T0 + timedelta(days=2)) == 1  # 1 jour
    assert j.mesurer_catalyseurs({"A": 110}, 51, maintenant=T0 + timedelta(days=8)) == 1  # 1 semaine
    effets = j.effet_par_categorie()
    # +10 % pour le titre, +2 % pour la référence : +8 points
    assert effets["contrat"] == {"cas": 1, "moyenne": pytest.approx(8), "positifs_pct": 100}
    assert "partenariat" in effets
    assert j.effet_par_categorie("ecart_4s") == {}


def test_poids_ajuste_seulement_avec_assez_de_cas():
    assert opp.ajuster_poids(20, {"cas": 3, "moyenne": 9}) == 20
    assert opp.ajuster_poids(20, {"cas": 12, "moyenne": 5}) == 30
    assert opp.ajuster_poids(20, {"cas": 12, "moyenne": -8}) == 10
    assert opp.ajuster_poids(20, None) == 20


def test_historique_change_le_score_et_s_explique():
    hist = {"INTC": decollage()}
    (neutre,) = opp.scorer(["INTC"], hist, [INTEL], set())
    (decevant,) = opp.scorer(["INTC"], hist, [INTEL], set(),
                             historique={"contrat": {"cas": 15, "moyenne": -6, "positifs_pct": 30}})
    assert decevant.score < neutre.score
    assert any("15 cas" in s for s in decevant.signaux)
