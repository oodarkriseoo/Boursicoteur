from boursicoteur import signaux
from boursicoteur.config import DEFAUTS
from boursicoteur.sources.actus import Actu
from conftest import serie

SEUILS = DEFAUTS["seuils"]


def test_alerte_chute_seance_et_prix_de_revient():
    hist = serie([100] * 30 + [70])
    portefeuille = [{"ticker": "ABC", "nom": "Abc", "prix_revient": 100}]
    cles = {a.cle for a in signaux.alertes_portefeuille(portefeuille, {"ABC": hist}, SEUILS)}
    assert cles == {"seance:ABC", "sommet:ABC:30", "revient:ABC:30"}


def test_palier():
    assert signaux.palier(-23, 10) == 20
    assert signaux.palier(-29.9, 10) == 20
    assert signaux.palier(-62, 10) == 60
    assert signaux.palier(-17, 5) == 15


def test_aucune_alerte_si_tout_va_bien(hausse):
    portefeuille = [{"ticker": "ABC", "prix_revient": 90}]
    assert signaux.alertes_portefeuille(portefeuille, {"ABC": hausse}, SEUILS) == []


def test_position_sans_cours_ignoree():
    assert signaux.alertes_portefeuille([{"ticker": "ABC"}], {}, SEUILS) == []


def test_alerte_vix():
    assert signaux.alerte_marche(serie([20, 35]), 30)[0].cle == "vix"
    assert signaux.alerte_marche(serie([20, 25]), 30) == []
    assert signaux.alerte_marche(None, 30) == []


def test_scan_ia_classe_la_meilleure_dynamique(hausse, baisse):
    actus = [Actu("Nvidia unveils new AI GPU", "http://x/1", "src", ticker="FORT")]
    pistes = signaux.scanner_theme_ia(
        ["FORT", "FAIBLE", "ABSENT"],
        {"FORT": hausse, "FAIBLE": baisse},
        actus,
        ["GPU"],
        seuil_volume=3,
    )
    assert [p.ticker for p in pistes] == ["FORT", "FAIBLE"]
    assert any("actu" in r for r in pistes[0].raisons)
    assert pistes[0].tendance == "haussière"
