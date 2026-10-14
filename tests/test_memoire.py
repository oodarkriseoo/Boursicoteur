from datetime import datetime, timedelta

from boursicoteur import memoire
from boursicoteur.journal import Hypothese, Journal

T0 = datetime(2026, 9, 1, 10)


def test_fiche_vide_puis_remplie():
    j = Journal(":memory:")
    assert "l'historique démarre" in memoire.fiche(j)

    j.noter_catalyseur("l1", "NVDA", ["contrat"], "Nvidia wins huge contract", 100, 50, le=T0)
    j.noter_catalyseur("l2", "MC.PA", ["résultats"], "LVMH beats", 400, 50, le=T0)
    j.mesurer_catalyseurs({"NVDA": 106, "MC.PA": 396}, 50, maintenant=T0 + timedelta(days=8))
    h = j.noter(Hypothese("NVDA", "hausse", 5, 3, "r", "i"), 100, 50, le=T0)
    j.evaluer(h, 106, 50)

    texte = memoire.fiche(j)
    assert "semi-conducteurs, contrat : +6.0 pts (1 cas)" in texte
    assert "luxe, résultats : -1.0 pts (1 cas)" in texte
    assert "1 évaluées, 100 % réussies" in texte
    assert "[NVDA] 2026-09-01 Nvidia wins huge contract (contrat) : 1 j +6.0 pts, 1 sem. +6.0 pts" in texte
    # filtrée sur une valeur
    assert "LVMH" not in memoire.fiche(j, ["NVDA"])


def test_secteur_surcharge():
    assert memoire.secteur("NVDA") == "semi-conducteurs"
    assert memoire.secteur("XYZ") == "autre"
    assert memoire.secteur("XYZ", {"XYZ": "défense"}) == "défense"
