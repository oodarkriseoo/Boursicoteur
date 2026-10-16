import numpy as np
import pytest

from boursicoteur import conseils as cons
from boursicoteur.config import DEFAUTS
from boursicoteur.journal import Journal
from conftest import serie

REGLAGES = DEFAUTS["conseils"]


def zigzag(depart, fin, n=80, amplitude=1.0):
    base = np.linspace(depart, fin, n)
    return serie(list(base + amplitude * np.array([1, -1] * (n // 2))))


def test_plan_achat_risque_borne(hausse):
    plan = cons.plan_achat("SOI.PA", zigzag(100, 120), REGLAGES)
    assert plan.devise == "EUR"
    assert plan.stop < plan.cours < plan.objectif
    # objectif à deux fois la distance au stop
    assert plan.objectif - plan.cours == pytest.approx(2 * (plan.cours - plan.stop))
    # perte au stop ≈ 2 % de la poche de 1000 €, frais compris, position ≤ 25 %
    assert plan.risque_eur <= 20.5
    assert plan.montant_eur <= 250.5
    assert "PEA possible" in plan.formater()


def test_plan_achat_en_dollars():
    plan = cons.plan_achat("NVDA", zigzag(100, 120), REGLAGES, taux=1.15)
    assert plan.devise == "USD"
    assert plan.quantite * plan.cours / 1.15 == pytest.approx(plan.montant_eur)
    assert "compte-titres" in plan.formater()


def test_plan_achat_action_trop_chere_non_fractionnable():
    assert cons.plan_achat("MC.PA", zigzag(5000, 5200), REGLAGES, fractionnable=False) is None


def test_avis_vendre_garder_prendre_des_gains(hausse, baisse):
    assert cons.avis_position({"ticker": "A"}, baisse, 160).action == "vendre"
    assert cons.avis_position({"ticker": "A"}, zigzag(100, 110), 100).action == "garder"
    # +60 % et surachat
    assert cons.avis_position({"ticker": "A"}, hausse, 100).action == "prendre des gains"
    texte = cons.formater_avis(cons.avis_position({"ticker": "A"}, zigzag(100, 110), 100), "Abc")
    assert texte.startswith("🟢 Abc (A) : garder") and "Sortie si clôture sous" in texte


def test_stop_personnel_releve_le_stop_suiveur():
    avis = cons.avis_position({"ticker": "A", "stop": 109}, zigzag(100, 110), 100)
    assert avis.stop_suiveur == 109


def test_suivi_et_bilan_des_conseils():
    from boursicoteur.cli import suivre_conseils

    journal = Journal(":memory:")
    assert journal.ouvrir_conseil("A", 100, 90, 120, 200, 2)
    assert journal.ouvrir_conseil("A", 100, 90, 120, 200, 2) is None  # déjà en cours
    journal.ouvrir_conseil("B", 50, 45, 60, 100, 2)

    assert suivre_conseils(journal, {"A": serie([100, 105]), "B": serie([50, 51])}) == []
    messages = suivre_conseils(journal, {"A": serie([100, 89]), "B": serie([50, 61])})
    assert "Vends A" in messages[0] and "Objectif atteint sur B" in messages[1]
    assert journal.conseils_ouverts() == []

    bilan = journal.bilan_conseils()
    # A : 200 × -11 % - 2 = -24 ; B : 100 × +22 % - 2 = +20
    assert bilan["clos"] == 2 and bilan["gagnants_pct"] == 50
    assert bilan["gain_net_eur"] == pytest.approx(-4)


def test_position_pea_de_long_terme_a_reevaluer_plutot_que_vendre(baisse):
    avis = cons.avis_position({"ticker": "MC.PA", "enveloppe": "PEA"}, baisse, 160)
    assert avis.action == "réévaluer" and avis.emoji == "🟡"
    assert "long terme" in avis.raisons[-1]
    # choix explicite : horizon court même dans le PEA
    assert cons.avis_position({"ticker": "MC.PA", "enveloppe": "PEA", "horizon": "court"},
                              baisse, 160).action == "vendre"


def test_baisse_de_fond_ne_donne_pas_garder():
    # deux ans de baisse puis un palier : MM20 et MM50 neutres, mais sous une MM200 qui baisse
    longue = list(np.linspace(200, 120, 420)) + [120 + (1 if i % 2 else -1) for i in range(80)]
    hist = serie(longue)
    assert cons._avis_technique({"ticker": "MC.PA"}, hist, 160).action == "alléger"
    avis = cons.avis_position({"ticker": "MC.PA", "enveloppe": "PEA"}, hist, 160)
    assert avis.action == "réévaluer"
    assert any("moyenne 200 jours" in r for r in avis.raisons)
    # historique trop court pour la MM200 : on reste sur la règle courte
    assert cons.avis_position({"ticker": "A"}, zigzag(100, 110), 100).action == "garder"
