import pytest

from boursicoteur import cli
from boursicoteur import portefeuille as ptf
from boursicoteur.journal import Journal
from conftest import serie

POSITIONS = [
    {"ticker": "MC.PA", "nom": "LVMH", "enveloppe": "PEA", "quantite": 2, "prix_revient": 600.0},
    {"ticker": "CW8.PA", "nom": "MSCI World", "enveloppe": "PEA", "quantite": 1, "prix_revient": 500.0},
    {"ticker": "NVDA", "nom": "Nvidia", "enveloppe": "CTO", "devise": "USD", "quantite": 10,
     "prix_revient": 100.0},
    {"ticker": "SPACEX", "nom": "SpaceX", "enveloppe": "CTO", "quantite": 1, "prix_revient": 200.0},
]
HISTORIQUES = {"MC.PA": serie([640, 650]), "CW8.PA": serie([510, 500]), "NVDA": serie([110, 132])}


def test_valeur_plus_value_et_poids():
    etat = ptf.valoriser(POSITIONS, HISTORIQUES, {"USD": 1.1})
    pea, cto = etat.enveloppes  # la plus grosse enveloppe d'abord
    assert pea.nom == "PEA" and pea.valeur_eur == 1800
    assert cto.valeur_eur == pytest.approx(1400)  # 10 × 132 $ / 1,1 + SpaceX au prix de revient
    assert etat.valeur_eur == pytest.approx(3200)
    assert pea.poids_pct == pytest.approx(56.25)

    lvmh = pea.lignes[0]
    assert lvmh.gain_eur == 100 and lvmh.gain_pct == pytest.approx(100 / 12)
    assert lvmh.poids_enveloppe_pct == pytest.approx(1300 / 18)
    assert lvmh.variation_jour_eur == 20

    nvda = cto.lignes[0]
    assert nvda.valeur_eur == pytest.approx(1200) and nvda.gain_eur == pytest.approx(200)
    assert nvda.variation_jour_eur == pytest.approx(200)

    spacex = cto.lignes[1]
    assert not spacex.cotee and spacex.gain_eur is None
    assert etat.gain_eur == pytest.approx(100 + 0 + 200 + 0)


def test_sans_taux_de_change_la_ligne_reste_au_prix_de_revient():
    etat = ptf.valoriser(POSITIONS[2:3], HISTORIQUES, {"USD": None})
    assert etat.valeur_eur == 1000 and not etat.lignes[0].cotee


def test_ligne_sans_prix_de_revient_ni_cours_ignoree():
    etat = ptf.valoriser([{"ticker": "X", "enveloppe": "PEA", "quantite": 3}], {}, {})
    assert etat.enveloppes == []
    assert "Aucune position" in ptf.formater(etat)


def test_formatage_et_alerte_de_concentration():
    texte = ptf.formater(ptf.valoriser(POSITIONS, HISTORIQUES, {"USD": 1.1}))
    assert texte.startswith("💼 3 200 €, plus-value +300 € (+11.1 %)")  # coût 2 700 € hors SpaceX
    assert "sans cours, comptées au prix de revient : SpaceX" in texte
    assert "• PEA : 1 800 € (56 %)" in texte
    assert "LVMH (MC.PA) : 1 300 €, 72 % de l'enveloppe, 41 % du total" in texte
    assert "SpaceX (SPACEX) : 200 €" in texte and "sans cours" in texte
    assert "⚖️ LVMH pèse 41 %, Nvidia pèse 38 %" in texte


def test_resume_commence_par_la_valeur():
    texte = cli.formater_resume("ctx", {}, [], [], valeur=["💼 3 200 €"])
    assert texte.startswith("💼 3 200 €")


def test_bouton_detail_du_portefeuille():
    from test_boutons import FauxNotifieur, clic

    notif = FauxNotifieur([clic("valeur:portefeuille")])
    cli.traiter_clics(Journal(":memory:"), notif, None, 1, portefeuille=POSITIONS,
                      valoriser=lambda pos: ptf.valoriser(pos, HISTORIQUES, {"USD": 1.1}))
    titre, texte = notif.envois[0]
    assert titre == "Ton portefeuille" and "Nvidia (NVDA)" in texte
