from boursicoteur import signaux
from boursicoteur.config import DEFAUTS
from boursicoteur.sources.actus import Actu
from conftest import serie

SEUILS = DEFAUTS["seuils"]


def test_alerte_chute_seance_et_prix_de_revient():
    hist = serie([100] * 30 + [70])
    portefeuille = [{"ticker": "ABC", "nom": "Abc", "prix_revient": 100}]
    cles = {a.cle for a in signaux.alertes_portefeuille(portefeuille, {"ABC": hist}, SEUILS)}
    assert cles == {"seance:ABC:30", "sommet:ABC:30", "revient:ABC:30"}


def test_prix_de_revient_en_euros_pour_une_valeur_en_dollars():
    hist = serie([115] * 30)  # cotée 115 $ ; achetée 100 € = 115 $ au taux de 1,15
    position = [{"ticker": "US", "prix_revient": 100, "devise": "USD"}]
    assert signaux.alertes_portefeuille(position, {"US": hist}, SEUILS, {"USD": 1.15}) == []
    hist = serie([80] * 30)  # 80 $ = -30 % en dollars
    cles = {a.cle for a in signaux.alertes_portefeuille(position, {"US": hist}, SEUILS, {"USD": 1.15})}
    assert "revient:US:30" in cles
    # taux indisponible : on s'abstient plutôt que de comparer des devises différentes
    assert signaux.alertes_portefeuille(position, {"US": hist}, SEUILS, {}) == []


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


def test_alerte_seance_renvoyee_si_la_chute_s_aggrave():
    position = [{"ticker": "ABC"}]
    cle = lambda dernier: {a.cle for a in signaux.alertes_portefeuille(
        position, {"ABC": serie([100] * 30 + [dernier])}, SEUILS)}
    assert "seance:ABC:3" in cle(95)
    assert "seance:ABC:9" in cle(90)


def test_chute_rapide_depuis_le_dernier_controle_avec_details():
    hist = serie([100] * 30 + [99, 95])
    actu = Actu("Abc cuts guidance", "http://x/abc", "src", ticker="ABC")
    alertes = signaux.alertes_portefeuille(
        [{"ticker": "ABC", "nom": "Abc", "prix_revient": 90}], {"ABC": hist}, SEUILS,
        cours_precedents={"ABC": 99}, titres={"ABC": actu},
    )
    rapide = next(a for a in alertes if a.cle.startswith("rapide:"))
    assert "a perdu 4.0 %" in rapide.message
    assert "+5.6 %" in rapide.message  # écart au prix de revient
    assert "Abc cuts guidance" in rapide.message
    # sans contrôle précédent, pas d'alerte de chute rapide
    assert not any(a.cle.startswith("rapide:") for a in signaux.alertes_portefeuille(
        [{"ticker": "ABC"}], {"ABC": hist}, SEUILS))


def test_seuil_de_sortie_personnel():
    hist = serie([100] * 30 + [97])
    alertes = signaux.alertes_portefeuille([{"ticker": "ABC", "stop": 98}], {"ABC": hist}, SEUILS)
    assert [a.cle for a in alertes] == ["stop:ABC"]
