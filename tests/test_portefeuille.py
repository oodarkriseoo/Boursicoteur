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
    from test_boutons import CONF, FauxNotifieur, clic, journal_avec_admin

    notif = FauxNotifieur([clic("valeur:portefeuille")])
    cli.traiter_mises_a_jour(CONF, journal_avec_admin(POSITIONS), notif, None, 1,
                             valoriser=lambda pos: ptf.valoriser(pos, HISTORIQUES, {"USD": 1.1}))
    titre, texte = notif.envois[0]
    assert titre == "Ton portefeuille" and "Nvidia (NVDA)" in texte


# --- historique de la valeur ----------------------------------------------------

from datetime import date, timedelta


def _photographier(journal, jour, positions, historiques, reference):
    conf = {"portefeuille": positions, "reference": "REF"}
    cli.noter_valeur(conf, journal, {**historiques, "REF": serie([reference])}, jour)


def test_un_versement_ne_compte_pas_comme_un_gain():
    journal = Journal(":memory:")
    lvmh = {"ticker": "MC.PA", "enveloppe": "PEA", "quantite": 2, "prix_revient": 600.0}
    _photographier(journal, date(2026, 10, 1), [lvmh], {"MC.PA": serie([600])}, 100)
    # LVMH +10 %, référence +5 %
    _photographier(journal, date(2026, 10, 2), [lvmh], {"MC.PA": serie([660])}, 105)
    # achat de 2 LVMH de plus (versement de 1 320 €), cours inchangé
    _photographier(journal, date(2026, 10, 3), [{**lvmh, "quantite": 4}], {"MC.PA": serie([660])}, 105)
    # vente totale de LVMH au même cours, achat de Sanofi : ni gain ni perte
    sanofi = {"ticker": "SAN.PA", "enveloppe": "PEA", "quantite": 30, "prix_revient": 88.0}
    _photographier(journal, date(2026, 10, 4), [sanofi], {"SAN.PA": serie([88])}, 105)

    photos = journal.photos()
    assert [p["apport_eur"] for p in photos] == [0, 0, pytest.approx(1320), pytest.approx(2640 - 2640)]
    serie_indices = ptf.indices(photos)
    assert serie_indices[-1][1] == pytest.approx(1.10)
    assert serie_indices[-1][2] == pytest.approx(1.05)


def test_pas_de_photo_quand_un_cours_manque_soudain(monkeypatch):
    monkeypatch.setattr(cli.src_cours, "historiques", lambda tickers, periode: {})  # Yahoo en panne
    journal = Journal(":memory:")
    lvmh = {"ticker": "MC.PA", "enveloppe": "PEA", "quantite": 2, "prix_revient": 600.0}
    _photographier(journal, date(2026, 10, 1), [lvmh], {"MC.PA": serie([700])}, 100)
    _photographier(journal, date(2026, 10, 2), [lvmh], {}, 100)  # Yahoo en panne
    assert len(journal.photos()) == 1


def test_la_derniere_photo_du_jour_l_emporte():
    journal = Journal(":memory:")
    lvmh = {"ticker": "MC.PA", "enveloppe": "PEA", "quantite": 1, "prix_revient": 600.0}
    _photographier(journal, date(2026, 10, 1), [lvmh], {"MC.PA": serie([600])}, 100)
    _photographier(journal, date(2026, 10, 1), [lvmh], {"MC.PA": serie([610])}, 100)
    assert [p["valeur_eur"] for p in journal.photos()] == [610]


def test_performance_par_periode_et_courbes():
    journal = Journal(":memory:")
    lvmh = {"ticker": "MC.PA", "enveloppe": "PEA", "quantite": 1, "prix_revient": 100.0}
    debut = date(2026, 9, 1)
    for i in range(44):
        _photographier(journal, debut + timedelta(days=i), [lvmh], {"MC.PA": serie([100 + i])}, 100 + i / 2)
    texte = ptf.formater_performance(journal.photos(), "CW8.PA", aujourd_hui=date(2026, 10, 14))
    assert texte.startswith("📈 Face à CW8.PA (historique depuis le 01/09/2026)")
    assert "• 1 semaine : toi +5.1 %, référence +3.0 %, écart +2.2 pts" in texte
    assert "• 1 mois" in texte and "• 3 mois" not in texte
    assert "• Depuis le début : toi +43.0 %, référence +21.5 %" in texte
    toi = texte.split("Toi        ")[1].split("\n")[0]
    assert len(toi) == 30 and toi.endswith("█") and "Référence  ▁" in texte


def test_premier_jour_d_historique():
    journal = Journal(":memory:")
    lvmh = {"ticker": "MC.PA", "enveloppe": "PEA", "quantite": 1, "prix_revient": 100.0}
    _photographier(journal, date(2026, 10, 14), [lvmh], {"MC.PA": serie([100])}, 100)
    assert "apparaît dès demain" in ptf.formater_performance(journal.photos(), "CW8.PA")
    assert ptf.formater_performance([], "CW8.PA") == ""
