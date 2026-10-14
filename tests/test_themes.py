from boursicoteur import config as cfg
from boursicoteur.cli import secteurs
from boursicoteur.opportunites import Opportunite, formater

CONF = cfg._fusionner(cfg.DEFAUTS, {
    "theme_ia": {"univers": ["NVDA", "SU.PA"], "mots_cles": ["GPU"]},
    "themes": {
        "sante": {"libelle": "Santé", "univers": ["SAN.PA", "LLY"]},
        "electrification": {"univers": ["SU.PA", "ENR.DE"]},
    },
    "secteurs": {"LLY": "pharma US"},
})


def test_themes_ia_en_premier_et_valeurs_par_defaut():
    themes = cfg.themes(CONF)
    assert list(themes) == ["ia", "sante", "electrification"]
    assert themes["ia"]["libelle"] == "IA"
    assert themes["electrification"]["libelle"] == "Electrification"
    assert themes["electrification"]["pistes_max"] == 3
    assert themes["sante"]["mots_cles"] == []


def test_univers_complet_sans_doublon():
    assert cfg.univers_complet(CONF) == ["NVDA", "SU.PA", "SAN.PA", "LLY", "ENR.DE"]
    assert cfg.tickers_suivis(CONF) == ["NVDA", "SU.PA", "SAN.PA", "LLY", "ENR.DE", "CW8.PA"]


def test_sans_section_themes_seule_l_ia_compte():
    conf = cfg._fusionner(cfg.DEFAUTS, {"theme_ia": {"univers": ["NVDA"]}})
    assert cfg.univers_complet(conf) == ["NVDA"]


def test_theme_de_garde_le_premier_theme():
    assert cfg.theme_de(CONF)["SU.PA"] == "IA"
    assert cfg.theme_de(CONF)["ENR.DE"] == "Electrification"


def test_secteurs_pour_la_memoire():
    s = secteurs(CONF)
    assert s["LLY"] == "pharma US"          # la configuration d'abord
    assert "SAN.PA" not in s                # memoire.SECTEURS le connaît déjà (santé)
    assert s["ENR.DE"] == "Electrification"  # sinon le thème


def test_alerte_porte_le_theme_et_l_enveloppe():
    o = Opportunite("SAN.PA", points={"catalyseur": 30}, theme="Santé")
    assert formater(o).startswith("💎 SAN.PA (Santé · PEA possible) : score 30/100")
    assert "(compte-titres)" in formater(Opportunite("LLY"))
