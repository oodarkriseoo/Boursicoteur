import numpy as np

from boursicoteur import cli, utilisateurs as usr
from boursicoteur.journal import Hypothese
from conftest import serie
from test_boutons import CONF, FauxNotifieur, journal_avec_admin
from test_utilisateurs import message


def test_seuls_les_changements_sont_signales():
    journal = journal_avec_admin([{"ticker": "ABC", "nom": "Abc", "quantite": 1, "prix_revient": 100,
                                   "horizon": "court"}])
    u = journal.utilisateur("x")
    conf_u = usr.conf_de(CONF, u)
    calme = {"ABC": serie(list(np.linspace(100, 130, 300))), CONF["reference"]: serie([100.0] * 300)}
    piste = Hypothese("ABC", "hausse", 20, 4, "contrat signé", "si annulé")

    # premier passage : rien à comparer, rien à dire
    assert cli.changements(journal, u, conf_u, calme, {}, [piste]) == []
    # même situation le lendemain : toujours rien, et la piste déjà vue n'est pas redite
    assert cli.changements(journal, u, conf_u, calme, {}, [piste]) == []

    chute = {**calme, "ABC": serie(list(np.linspace(100, 130, 290)) + list(np.linspace(128, 95, 10)))}
    baisse = Hypothese("ABC", "baisse", 10, 3, "concurrent moins cher", "si marges tenues")
    lignes = cli.changements(journal, u, conf_u, chute, {}, [piste, baisse])
    assert any("Abc (ABC)" in l and "vendre" in l and "(avant : prendre des gains)" in l for l in lignes)
    assert any(l.startswith("🌡️ Abc : risque de baisse") for l in lignes)
    assert [l for l in lignes if "possible" in l] == [
        "⬇️ Baisse possible : ABC (ABC), 10 j, confiance 3/5. concurrent moins cher"]


def test_resume_complet_a_la_demande():
    journal = journal_avec_admin()
    notif = FauxNotifieur([], [message("/resume", chat="x")])
    cli.traiter_mises_a_jour(CONF, journal, notif, None, 1)
    assert notif.envois[0][1].startswith("Pas encore de résumé")
    journal.retenir("resume:x", {"titre": "Résumé du 21/10/2026", "texte": "🌍 Contexte"})
    notif = FauxNotifieur([], [message("/resume", chat="x")])
    cli.traiter_mises_a_jour(CONF, journal, notif, None, 1)
    assert notif.envois[0] == ("Résumé du 21/10/2026", "🌍 Contexte")
