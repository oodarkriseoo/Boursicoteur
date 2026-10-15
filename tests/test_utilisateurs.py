import sqlite3

from boursicoteur import cli, utilisateurs as usr
from boursicoteur.journal import Journal
from boursicoteur.notifications import Notifieur
from conftest import serie
from test_boutons import CONF, FauxNotifieur, clic, journal_avec_admin


def message(texte, chat="b", prenom="Bob"):
    return {"chat": {"id": chat, "type": "private"}, "from": {"first_name": prenom}, "text": texte}


def traiter(journal, clics=(), messages=(), valider=lambda t: t != "FAUX.PA"):
    notif = FauxNotifieur(list(clics), messages)
    cli.traiter_mises_a_jour(CONF, journal, notif, None, 1, valider=valider)
    return notif


def test_parcours_d_un_nouvel_utilisateur():
    journal = journal_avec_admin([{"ticker": "MC.PA", "quantite": 1, "prix_revient": 600}])

    # un inconnu ne peut rien faire avant d'être accepté
    notif = traiter(journal, messages=[message("/ajouter NVDA 2 100")])
    assert "/start" in notif.envois[0][1]
    notif = traiter(journal, messages=[message("/start")])
    assert notif.destinataires == ["b", "x"]
    assert "Bob demande" in notif.envois[1][1]
    assert journal.utilisateur("b")["statut"] == usr.EN_ATTENTE
    notif = traiter(journal, clics=[clic("accepter:b", chat="b")])  # pas à lui d'accepter
    assert journal.utilisateur("b")["statut"] == usr.EN_ATTENTE

    notif = traiter(journal, clics=[clic("accepter:b")])
    assert journal.utilisateur("b")["statut"] == usr.ACTIF
    assert notif.destinataires == ["b"] and "Accès accepté" in notif.envois[0][1]

    notif = traiter(journal, messages=[
        message("/ajouter NVDA 2 100,5 CTO Nvidia Corp"),
        message("/ajouter FAUX.PA 1 10"),
        message("/suivre ASML.AS SAF.PA FAUX.PA"),
        message("/stop NVDA 90"),
        message("/positions"),
    ])
    textes = [t for _, t in notif.envois]
    assert "Position ajoutée" in textes[0] and "Pas de cours pour FAUX.PA" in textes[1]
    assert "ASML.AS, SAF.PA" in textes[2] and "FAUX.PA" in textes[2]
    bob = journal.utilisateur("b")
    assert bob["portefeuille"] == [{"ticker": "NVDA", "nom": "Nvidia Corp", "enveloppe": "CTO", "quantite": 2.0,
                                    "prix_revient": 100.5, "devise": "USD", "stop": 90.0}]
    assert bob["suivis"] == ["ASML.AS", "SAF.PA"]
    assert "Nvidia Corp (NVDA), CTO : 2 à 100.5 €" in textes[4]
    # le portefeuille de l'administrateur n'a pas bougé
    assert [p["ticker"] for p in journal.utilisateur("x")["portefeuille"]] == ["MC.PA"]

    # /quitter demande confirmation, puis efface tout
    journal.ouvrir_conseil("NVDA", 100, 90, 120, 200, 2, chat_id="b")
    notif = traiter(journal, messages=[message("/quitter")])
    assert notif.envois[0][1].startswith("Effacer")
    traiter(journal, clics=[clic("quitter:b", chat="b")])
    assert journal.utilisateur("b") is None and journal.conseils_ouverts("b") == []


def test_chacun_ne_touche_qu_a_ses_conseils():
    journal = journal_avec_admin()
    journal.enregistrer_utilisateur({"chat_id": "b", "nom": "Bob", "statut": usr.ACTIF})
    id_admin = journal.ouvrir_conseil("A", 100, 90, 120, 200, 2, chat_id="x")
    notif = traiter(journal, clics=[clic(f"achete:{id_admin}", chat="b"), clic("valeur:portefeuille", chat="z")])
    assert notif.accuses == ["Bouton inconnu ou expiré.", "Accès non autorisé."]
    assert journal.conseil(id_admin)["decision"] is None
    # un même ticker peut faire l'objet d'un conseil pour chacun
    assert journal.ouvrir_conseil("A", 100, 90, 120, 200, 2, chat_id="b")


def test_alertes_envoyees_a_ceux_qui_detiennent_ou_surveillent(monkeypatch):
    journal = journal_avec_admin([{"ticker": "CHUTE", "nom": "Chute SA", "quantite": 1, "prix_revient": 100}])
    journal.enregistrer_utilisateur({"chat_id": "b", "nom": "Bob", "statut": usr.ACTIF, "suivis": ["CHUTE"]})
    journal.enregistrer_utilisateur({"chat_id": "c", "nom": "Chloé", "statut": usr.ACTIF,
                                     "portefeuille": [{"ticker": "STABLE", "quantite": 1, "prix_revient": 10}]})
    cours = [100.0] * 60 + [80.0]
    monkeypatch.setattr(cli.src_cours, "historiques", lambda tickers, periode: {
        "CHUTE": serie(cours), "STABLE": serie([10.0] * 61), CONF["reference"]: serie([100.0] * 61)})
    monkeypatch.setattr(cli.src_actus, "collecter", lambda flux, tickers: [])
    notif = FauxNotifieur([])
    cli.veille(CONF, journal, notif)
    alertes = [(d, t) for d, (titre, t) in zip(notif.destinataires, notif.envois) if titre == "Alerte"]
    assert {d for d, _ in alertes} == {"x", "b"}
    assert any("prix de revient" in t for d, t in alertes if d == "x")
    assert not any("prix de revient" in t for d, t in alertes if d == "b")  # Bob ne la détient pas
    # chaque photo du portefeuille est rangée chez son propriétaire
    assert len(journal.photos("x")) == 1 and len(journal.photos("c")) == 1 and journal.photos("b") == []

    nombre = len(notif.envois)
    cli.veille(CONF, journal, notif)
    assert len([t for t, _ in notif.envois[nombre:] if t == "Alerte"]) == 0  # pas de doublon


def test_destinataires():
    a = {"chat_id": "a", "portefeuille": [{"ticker": "X"}], "suivis": []}
    b = {"chat_id": "b", "portefeuille": [], "suivis": ["Y"]}
    assert usr.destinataires([a, b], "T", {"T"}) == [a, b]
    assert usr.destinataires([a, b], "Y", {"T"}) == [b]
    assert usr.destinataires([a, b], "Z", {"T"}) == []


def test_conf_de_utilise_la_poche_de_l_utilisateur():
    u = {"portefeuille": [{"ticker": "X"}], "suivis": ["Y"], "poche_eur": 500}
    conf = usr.conf_de(CONF, u)
    assert conf["conseils"]["poche_eur"] == 500 and conf["portefeuille"] == [{"ticker": "X"}]
    assert CONF["conseils"]["poche_eur"] == 1000  # la configuration commune reste intacte


def test_administrateur_cree_une_fois_depuis_config():
    journal = Journal(":memory:")
    journal.photographier(__import__("datetime").date(2026, 10, 1), 100, 0, 50, {})
    usr.preparer(journal, {"portefeuille": [{"ticker": "MC.PA"}]}, "42")
    usr.preparer(journal, {"portefeuille": [{"ticker": "AUTRE"}]}, "42")  # config.yaml n'est relu qu'une fois
    admin = journal.utilisateur("42")
    assert admin["statut"] == usr.ADMIN and admin["portefeuille"] == [{"ticker": "MC.PA"}]
    assert len(journal.photos("42")) == 1


def test_migration_des_photos_d_avant(tmp_path):
    chemin = tmp_path / "j.sqlite"
    db = sqlite3.connect(chemin)
    db.execute("CREATE TABLE valeurs_portefeuille (jour TEXT PRIMARY KEY, valeur_eur REAL NOT NULL, "
               "apport_eur REAL NOT NULL, reference REAL NOT NULL, lignes TEXT NOT NULL)")
    db.execute("INSERT INTO valeurs_portefeuille VALUES ('2026-10-01', 1000, 0, 50, '{}')")
    db.commit()
    db.close()
    journal = Journal(chemin)
    usr.preparer(journal, {"portefeuille": []}, "42")
    assert [p["valeur_eur"] for p in journal.photos("42")] == [1000]


def test_messages_de_groupe_ignores(monkeypatch):
    monkeypatch.setenv("TG_T", "jeton")
    monkeypatch.setenv("TG_C", "123")

    class Reponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"result": [
                {"update_id": 1, "message": {"text": "/positions", "chat": {"id": -5, "type": "group"}}},
                {"update_id": 2, "message": {"text": "/positions", "chat": {"id": 7, "type": "private"}}},
                {"update_id": 3, "callback_query": {"id": "c", "data": "x"}},
            ]}

    monkeypatch.setattr("boursicoteur.notifications.requests.get", lambda *a, **k: Reponse())
    clics, messages, offset = Notifieur({"telegram": {"token_env": "TG_T", "chat_id_env": "TG_C"}}).mises_a_jour(None, 1)
    assert offset == 4 and len(clics) == 1 and [m["chat"]["id"] for m in messages] == [7]


def test_envoi_vers_un_autre_utilisateur_sans_ntfy(monkeypatch):
    monkeypatch.setenv("TG_T", "jeton")
    monkeypatch.setenv("TG_C", "123")
    monkeypatch.setenv("NTFY", "sujet")
    envois = []
    monkeypatch.setattr(Notifieur, "_poster", staticmethod(lambda url, **kw: envois.append((url, kw))))
    n = Notifieur({"telegram": {"token_env": "TG_T", "chat_id_env": "TG_C"}, "ntfy": {"topic_env": "NTFY"}})
    n.envoyer("texte", chat_id="456")
    assert len(envois) == 1 and envois[0][1]["json"]["chat_id"] == "456"
    n.envoyer("texte")
    assert len(envois) == 3  # l'administrateur reçoit aussi ntfy
