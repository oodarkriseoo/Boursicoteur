import pytest

from boursicoteur import indicateurs as ind
from conftest import serie


def test_variation_seance():
    assert ind.variation_seance_pct(serie([100, 95])) == pytest.approx(-5)
    assert ind.variation_seance_pct(serie([100])) is None


def test_variation_sur_periode():
    assert ind.variation_sur_pct(serie([100, 110, 120]), 2) == pytest.approx(20)
    assert ind.variation_sur_pct(serie([100, 110]), 2) is None


def test_ecart_au_plus_haut():
    assert ind.ecart_au_plus_haut_pct(serie([100, 200, 150])) == pytest.approx(-25)


def test_rsi_extremes(hausse, baisse):
    assert ind.rsi(hausse) == 100
    assert ind.rsi(baisse) < 1


def test_ratio_volume():
    volumes = [1000] * 20 + [5000]
    assert ind.ratio_volume(serie([10] * 21, volumes)) == pytest.approx(5)


def test_tendance(hausse, baisse):
    assert ind.tendance(hausse) == "haussière"
    assert ind.tendance(baisse) == "baissière"
    assert ind.tendance(serie([100] * 10)) == "neutre"
