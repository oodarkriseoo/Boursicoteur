import sqlite3

from boursicoteur import cli, config as cfg, utilisateurs as usr
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


CONF_THEMES = cfg._fusionner(CONF, {"theme_ia": {"univers": ["NVDA"]},
                                     "themes": {"sante": {"libelle": "Santé", "univers": ["SAN.PA", "LLY"]}}})


def test_destinataires_selon_les_domaines_choisis():
    a = {"chat_id": "a", "portefeuille": [{"ticker": "X"}], "suivis": [], "themes": None}  # tous les domaines
    b = {"chat_id": "b", "portefeuille": [], "suivis": ["Y"], "themes": ["sante"]}
    assert usr.destinataires([a, b], "LLY", CONF_THEMES) == [a, b]
    assert usr.destinataires([a, b], "NVDA", CONF_THEMES) == [a]  # Bob ne suit pas l'IA
    assert usr.destinataires([a, b], "Y", CONF_THEMES) == [b]
    assert usr.destinataires([a, b], "Z", CONF_THEMES) == []


def test_commande_themes_et_choix_des_domaines():
    journal = journal_avec_admin()
    journal.enregistrer_utilisateur({"chat_id": "b", "nom": "Bob", "statut": usr.ACTIF})
    hausse = serie([100.0 + i for i in range(60)])

    def faire(clics=(), messages=()):
        notif = FauxNotifieur(list(clics), messages)
        cli.traiter_mises_a_jour(CONF_THEMES, journal, notif, None, 1, collecter=lambda f, t: [],
                                 telecharger=lambda tickers, periode: {t: hausse for t in tickers if t != "LLY"})
        return notif

    notif = faire(messages=[message("/themes")])
    assert "🔔 IA : 1 valeurs" in notif.envois[0][1] and "🔔 Santé : 2 valeurs" in notif.envois[0][1]

    notif = faire(clics=[clic("theme:sante", chat="b")])
    texte = notif.envois[0][1]
    assert "1. Sanofi (SAN.PA) · PEA possible" in texte and "Sans cours aujourd'hui : LLY" in texte

    faire(clics=[clic("interet:ia", chat="b")])  # Bob ne veut plus l'IA
    assert journal.utilisateur("b")["themes"] == ["sante"]
    assert "🔕 IA" in faire(messages=[message("/themes")]).envois[0][1]
    faire(clics=[clic("interet:ia", chat="b")])  # retour à « tous »
    assert journal.utilisateur("b")["themes"] is None

    faire(clics=[clic("suivre:SAN.PA,PIRATE", chat="b")])
    assert journal.utilisateur("b")["suivis"] == ["SAN.PA"]  # rien en dehors des thèmes


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


def test_chercher_par_secteur_et_pays():
    from boursicoteur import annuaire

    assert [s.ticker for s in annuaire.chercher("defense france")] == ["SAF.PA", "HO.PA"]  # une cotation par société
    assert [s.ticker for s in annuaire.chercher("pharmacie usa")] == ["LLY"]
    assert annuaire.nom("LLY") == "Eli Lilly" and annuaire.cle("Eli Lilly and Company") == "eli lilly"

    journal = journal_avec_admin()
    notif = traiter(journal, messages=[message("/chercher defense france", chat="x")])
    texte = notif.envois[0][1]
    assert "SAF.PA : Safran, Aerospace & Defense, France" in texte
    traiter(journal, clics=[clic("suivre:HO.PA")])
    assert journal.utilisateur("x")["suivis"] == ["HO.PA"]
    assert "Thales (HO.PA)" in traiter(journal, messages=[message("/liste", chat="x")]).envois[0][1]


def test_stop_suiveur_et_etf_par_commande():
    journal = journal_avec_admin([{"ticker": "CW8.PA", "quantite": 1, "prix_revient": 500, "enveloppe": "PEA"}])
    notif = traiter(journal, messages=[
        message("/suiveur CW8.PA 12", chat="x"),
        message("/etf CW8.PA", chat="x"),
        message("/ajouter CW8.PA 2 510 PEA", chat="x"),  # la mise à jour garde les réglages
        message("/suiveur NVDA 10", chat="x"),
        message("/suiveur CW8.PA 0", chat="x"),
    ])
    textes = [t for _, t in notif.envois]
    assert "-12 %" in textes[0] and "marqué comme ETF" in textes[1]
    assert "stop suiveur 12 %, ETF" in textes[2]
    assert "-10 %" in textes[3]  # NVDA, non détenue, passe en valeur surveillée
    admin = journal.utilisateur("x")
    (ligne,) = admin["portefeuille"]
    assert ligne["diversifie"] and "suiveur_pct" not in ligne and ligne["quantite"] == 2
    assert admin["suivis"] == ["NVDA"] and admin["alertes_suivis"] == {"NVDA": {"suiveur_pct": 10.0}}


def test_seuils_d_alerte_sur_une_valeur_seulement_surveillee():
    journal = journal_avec_admin()
    notif = traiter(journal, messages=[
        message("/baisse WLD.PA 1", chat="x"),
        message("/stop WLD.PA 400", chat="x"),
        message("/baisse FAUX.PA 1", chat="x"),
        message("/baisse NVDA 0", chat="x"),
        message("/liste", chat="x"),
    ])
    textes = [t for _, t in notif.envois]
    assert "perd 1 % sur la séance" in textes[0] and "alerte sous 400" in textes[1]
    assert "Pas de cours pour FAUX.PA" in textes[2] and "ni dans tes positions" in textes[3]
    assert "MSCI World (WLD.PA) [alerte sous 400, séance -1 %]" in textes[4]
    admin = journal.utilisateur("x")
    assert admin["suivis"] == ["WLD.PA"]
    assert admin["alertes_suivis"] == {"WLD.PA": {"baisse_pct": 1.0, "stop": 400.0}}

    traiter(journal, messages=[message("/baisse WLD.PA 0", chat="x"), message("/stop WLD.PA 0", chat="x")])
    assert journal.utilisateur("x")["alertes_suivis"] == {}
    traiter(journal, messages=[message("/baisse WLD.PA 2", chat="x"), message("/oublier WLD.PA", chat="x")])
    admin = journal.utilisateur("x")
    assert admin["suivis"] == [] and admin["alertes_suivis"] == {}


def donnees(boutons):
    return [d for ligne in boutons for _, d in ligne]


def test_alerte_par_le_nom_ou_tout_en_boutons():
    journal = journal_avec_admin([{"ticker": "MC.PA", "nom": "LVMH", "quantite": 1, "prix_revient": 600}])
    notif = traiter(journal, messages=[message("/alerte", chat="x")])
    choix = donnees(notif.boutons[0])
    assert choix[0] == "alerte:MC.PA" and "alerte:WLD.PA" in choix and "alerte:^FCHI" in choix

    # bouton « MSCI World », puis « -2 % »
    notif = traiter(journal, clics=[clic("alerte:WLD.PA")])
    assert "MSCI World" in notif.envois[0][1] and "baisse:WLD.PA:2" in donnees(notif.boutons[0])
    notif = traiter(journal, clics=[clic("baisse:WLD.PA:2")])
    assert "MSCI World (WLD.PA) perd 2 %" in notif.accuses[0]
    admin = journal.utilisateur("x")
    assert admin["suivis"] == ["WLD.PA"] and admin["alertes_suivis"] == {"WLD.PA": {"baisse_pct": 2.0}}

    # en texte, par le nom : position, indice, annuaire
    notif = traiter(journal, messages=[
        message("/alerte lvmh 3", chat="x"),
        message("/baisse cac 40 1,5", chat="x"),
        message("/stop sanofi 80", chat="x"),
        message("/alerte safran", chat="x"),
        message("/alerte france", chat="x"),
    ])
    textes = [t for _, t in notif.envois]
    assert "LVMH (MC.PA) perd 3 %" in textes[0] and "CAC 40 (^FCHI) perd 1.5 %" in textes[1]
    assert "SAN.PA" in textes[2]
    assert "Safran" in textes[3] and "baisse:SAF.PA:1" in donnees(notif.boutons[3])
    assert textes[4] == "Laquelle ?" and "alerte:HO.PA" in donnees(notif.boutons[4])
    admin = journal.utilisateur("x")
    assert admin["portefeuille"][0]["baisse_pct"] == 3.0
    assert admin["alertes_suivis"]["SAN.PA"] == {"stop": 80.0}

    notif = traiter(journal, clics=[clic("baisse:WLD.PA:0"), clic("baisse:WLD.PA:abc")])
    assert "enlevée" in notif.accuses[0] and "expiré" in notif.accuses[1]


def test_alerte_de_baisse_choisie_meme_si_la_baisse_est_ordinaire(monkeypatch):
    journal = journal_avec_admin()
    journal.enregistrer_utilisateur({**journal.utilisateur("x"), "suivis": ["WLD.PA"],
                                     "alertes_suivis": {"WLD.PA": {"baisse_pct": 1.0, "suiveur_pct": 5.0}}})
    # une valeur qui oscille de ±1,5 % chaque jour : -1,5 % est sa respiration normale
    cours = [100.0 * (1.015 if i % 2 else 1) for i in range(80)] + [100.0 * 1.015 * 0.985]
    monkeypatch.setattr(cli.src_cours, "historiques", lambda tickers, periode: {
        "WLD.PA": serie(cours), CONF["reference"]: serie([100.0] * 81)})
    monkeypatch.setattr(cli.src_actus, "collecter", lambda flux, tickers: [])
    notif = FauxNotifieur([])
    cli.veille(CONF, journal, notif)
    alertes = [t for titre, t in notif.envois if titre == "Alerte"]
    assert any("WLD.PA" in t and "-1.5 % sur la séance" in t and "-1 %" in t for t in alertes)
    assert not any("Stop suiveur" in t for t in alertes)  # à -1,5 % du sommet, loin des -5 %
