from boursicoteur import garde_fous as gf
from boursicoteur import portefeuille as ptf
from boursicoteur.cli import formater_resume
from boursicoteur.config import DEFAUTS
from conftest import serie

CONF = {"seuils": DEFAUTS["seuils"]}


def test_concentration_au_dessus_du_plafond_sauf_etf():
    portefeuille = [
        {"ticker": "LOURD.PA", "nom": "Lourd", "quantite": 60},
        {"ticker": "ETF.PA", "nom": "Etf", "quantite": 30, "diversifie": True},
        {"ticker": "LEGER.PA", "nom": "Leger", "quantite": 10},
    ]
    historiques = {t: serie([100] * 5) for t in ("LOURD.PA", "ETF.PA", "LEGER.PA")}
    etat = ptf.valoriser(portefeuille, historiques, {})
    alertes = gf.alertes_concentration(etat, portefeuille, 25)
    assert [a.cle for a in alertes] == ["concentration:LOURD.PA:60"]  # l'ETF à 30 % n'alerte pas
    # vendre 4 667 € : (6 000 - 4 667) / (10 000 - 4 667) = 25 %
    assert "environ 4667 €" in alertes[0].message
    assert alertes[0].unique
    # une seule ligne : pas de plafond qui tienne
    seule = portefeuille[:1]
    assert gf.alertes_concentration(ptf.valoriser(seule, historiques, {}), seule, 25) == []
    assert gf.alertes_concentration(None, portefeuille, 25) == []


def test_stop_suiveur_depuis_la_date_d_achat():
    hist = serie([200] + [100] * 10 + [120, 110, 105])
    position = {"ticker": "X", "nom": "X", "suiveur_pct": 10, "achat_le": str(hist.index[1].date())}
    (s,) = gf.stops_suiveurs([position], {"X": hist})
    assert s.sommet == 120  # le 200 d'avant l'achat ne compte pas
    assert round(s.niveau, 2) == 108 and s.touche
    (alerte,) = gf.alertes_suiveurs([s])
    assert alerte.cle == "suiveur:X:120"
    # sans date d'achat : plus haut des 6 derniers mois, ici 200
    (s,) = gf.stops_suiveurs([{**position, "achat_le": None}], {"X": hist})
    assert s.sommet == 200


def test_pas_de_stop_suiveur_sans_reglage():
    assert gf.stops_suiveurs([{"ticker": "X"}], {"X": serie([100] * 5)}) == []


def test_recul_du_portefeuille_avec_change_historique():
    portefeuille = [{"ticker": "A.PA", "quantite": 10}, {"ticker": "US", "quantite": 10, "devise": "USD"}]
    historiques = {
        "A.PA": serie([100, 100, 100, 90]),
        "US": serie([100, 100, 100, 100]),
        "EURUSD=X": serie([1.0, 1.0, 1.0, 1.25]),  # le dollar baisse : la ligne US perd 20 % en euros
    }
    valeur = gf.valeur_historique(portefeuille, historiques, {"USD": 1.25})
    assert list(valeur.round(2)) == [2000, 2000, 2000, 1700]
    r = gf.recul(valeur)
    assert round(r.pct, 1) == -15.0
    (alerte,) = gf.alerte_recul(r, 10)
    assert alerte.cle == f"recul:{r.date_sommet}:15"
    assert gf.alerte_recul(r, 20) == []


def test_eligibilite_pea():
    portefeuille = [
        {"ticker": "MC.PA", "enveloppe": "PEA"},
        {"ticker": "NOVO-B.CO", "enveloppe": "PEA"},      # Danemark : Union européenne
        {"ticker": "NVDA", "nom": "Nvidia", "enveloppe": "PEA"},
        {"ticker": "BTC-EUR", "enveloppe": "CTO"},
        {"ticker": "SAN.PA", "nom": "Sanofi", "enveloppe": "CTO"},
    ]
    assert [a.cle for a in gf.verifier_pea(portefeuille)] == ["pea:NVDA"]
    assert gf.bascules_pea(portefeuille) == ["Sanofi"]


def test_synthese_dans_le_resume():
    conf = {**CONF, "portefeuille": [
        {"ticker": "A.PA", "nom": "A", "quantite": 30, "enveloppe": "PEA", "suiveur_pct": 15},
        {"ticker": "B.PA", "nom": "B", "quantite": 10, "enveloppe": "PEA"},
    ]}
    historiques = {"A.PA": serie([100] * 5), "B.PA": serie([100] * 5)}
    lignes = gf.synthese(conf, historiques, {}, ptf.valoriser(conf["portefeuille"], historiques, {}))
    assert lignes[0] == "⚖️ A pèse 75 % du portefeuille (plafond 25 %) : alléger d'environ 2667 € pour y revenir"
    assert not any(l.startswith("⚖️ B") for l in lignes)
    assert any(l.startswith("🪜 A : stop suiveur 85.00") for l in lignes)
    texte = formater_resume("ctx", {}, [], [], garde_fous=lignes)
    assert "🛡️ Garde-fous" in texte
    assert gf.synthese({**CONF, "portefeuille": []}, {}, {}) == []
