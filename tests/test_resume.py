from boursicoteur.opportunites import Opportunite
from boursicoteur.cli import formater_resume


def test_introductions_retenues_par_l_ia_affichees():
    texte = formater_resume("ctx", [], [], [{"societe": "Acme AI", "pourquoi": "p", "risques": "r"}])
    assert "Introductions en bourse à suivre" in texte
    assert "Acme AI" in texte


def test_aucune_rubrique_sans_introduction_retenue():
    assert "Introductions" not in formater_resume("ctx", [], [], [])


def test_classement_sur_100():
    o = Opportunite("NVDA", points={"catalyseur": 30, "dynamique": 20}, catalyseurs=["partenariat"])
    texte = formater_resume("ctx", [o], [], [])
    assert "NVDA 50/100 : catalyseur partenariat" in texte
