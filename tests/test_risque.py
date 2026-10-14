import numpy as np
import pytest

from boursicoteur import risque as rsq
from boursicoteur import signaux
from boursicoteur.config import DEFAUTS
from conftest import serie

SEUILS = DEFAUTS["seuils"]


def vagues(cycles=15, fin=None):
    """Hausse par vagues : +8 % sur 20 séances puis -5 % sur 8, comme le MSCI World."""
    cours = [100.0]
    for _ in range(cycles):
        cours += list(cours[-1] * np.linspace(1, 1.08, 21)[1:])
        cours += list(cours[-1] * np.linspace(1, 0.95, 9)[1:])
    for variation in fin or []:
        cours.append(cours[-1] * (1 + variation / 100))
    return serie(cours)


def test_replis_passes():
    assert rsq.replis_passes(serie([100, 90, 110, 99, 120])["Close"]) == pytest.approx([-10, -10])


def test_repli_habituel_score_bas():
    r = rsq.evaluer("W", vagues())
    assert r.repli_habituel_pct is not None and -6 < r.repli_habituel_pct < -4
    assert r.score < DEFAUTS["seuils"]["risque_alerte"]


def test_repli_hors_norme_score_haut_et_alerte():
    hist = vagues(fin=[-3, -3, -3, -3, -3, -3])
    r = rsq.evaluer("W", hist)
    assert r.centile_repli >= 90
    assert r.structure_cassee
    assert r.score >= DEFAUTS["seuils"]["risque_alerte"]
    alertes = signaux.alertes_portefeuille([{"ticker": "W", "nom": "World", "prix_revient": 120}],
                                           {"W": hist}, SEUILS)
    alerte = next(a for a in alertes if a.cle.startswith("risque:W"))
    assert "Baisse inhabituelle" in alerte.message
    assert "ton gain maximal" in alerte.message


def test_effet_sur_gains():
    cloture = serie([100, 150, 125])["Close"]
    assert "effacé 50 %" in rsq.effet_sur_gains(cloture, 100)
    assert "Tout ton gain" in rsq.effet_sur_gains(serie([100, 150, 90])["Close"], 100)
    assert rsq.effet_sur_gains(cloture, None) is None


def test_baisse_de_seance_jugee_selon_la_valeur():
    rng = np.random.default_rng(0)
    nerveuse = list(100 * np.cumprod(1 + rng.choice([-0.04, 0.045], 120)))
    nerveuse.append(nerveuse[-1] * 0.945)
    calme = [100 + 0.1 * (i % 2) for i in range(120)] + [94.5]
    cles = lambda cours: {a.cle.split(":")[0] for a in signaux.alertes_portefeuille(
        [{"ticker": "X"}], {"X": serie(cours)}, SEUILS)}
    assert "seance" not in cles(nerveuse)  # -5,5 % est une journée ordinaire pour elle
    assert "seance" in cles(calme)
