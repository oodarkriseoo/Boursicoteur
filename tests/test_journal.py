from datetime import date, datetime, timedelta

import pytest

from boursicoteur.journal import Hypothese, Journal


@pytest.fixture
def journal():
    return Journal(":memory:")


def test_actus_dedoublonnees(journal):
    assert journal.nouvelles_actus(["a", "b"]) == {"a", "b"}
    assert journal.nouvelles_actus(["b", "c"]) == {"c"}


def test_alerte_une_fois_par_jour(journal):
    jour = date(2026, 10, 14)
    assert journal.alerte_inedite("vix", "msg", jour)
    assert not journal.alerte_inedite("vix", "msg", jour)
    assert journal.alerte_inedite("vix", "msg", date(2026, 10, 15))


def test_alerte_unique_pas_renvoyee_le_lendemain(journal):
    assert journal.alerte_inedite("revient:ABC:20", "msg", date.min)
    assert not journal.alerte_inedite("revient:ABC:20", "msg", date.min)
    assert journal.alerte_inedite("revient:ABC:30", "msg", date.min)


def test_cycle_hypotheses_et_bilan(journal):
    creee = datetime(2026, 9, 1)
    h_ok = journal.noter(Hypothese("A", "hausse", 10, 3, "r", "i"), 100, 50, le=creee)
    h_ko = journal.noter(Hypothese("B", "baisse", 10, 2, "r", "i"), 100, 50, le=creee)
    journal.noter(Hypothese("C", "hausse", 90, 2, "r", "i"), 100, 50, le=creee)

    a_evaluer = journal.a_evaluer(date(2026, 9, 20))
    assert {l["ticker"] for l in a_evaluer} == {"A", "B"}

    assert journal.evaluer(h_ok, 110, 51) is True
    assert journal.evaluer(h_ko, 105, 51) is False

    stats = journal.bilan()
    assert stats["evaluees"] == 2
    assert stats["taux_reussite_pct"] == 50
    # A : +10 %, B (baisse) : le cours a pris 5 %, donc -5 % → moyenne +2,5 %
    assert stats["gain_moyen_pct"] == pytest.approx(2.5)
    assert stats["reference_moyenne_pct"] == pytest.approx(2)


def test_bilan_vide(journal):
    assert journal.bilan()["evaluees"] == 0


def test_cours_du_controle_precedent(journal):
    t0 = datetime(2026, 10, 14, 10)
    assert journal.echanger_cours({"A": 100}, maintenant=t0) == {}
    assert journal.echanger_cours({"A": 96}, maintenant=t0 + timedelta(minutes=30)) == {"A": 100}
    # le lendemain matin, l'écart de la nuit n'est pas une chute rapide
    assert journal.echanger_cours({"A": 90}, maintenant=t0 + timedelta(hours=20)) == {}
