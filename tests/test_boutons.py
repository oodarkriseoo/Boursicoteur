import sqlite3

from boursicoteur import cli
from boursicoteur.journal import Journal
from boursicoteur.notifications import Notifieur
from conftest import serie


class FauxNotifieur:
    token = chat_id = "x"

    def __init__(self, clics):
        self._clics, self.accuses = clics, []

    def clics(self, offset, attente):
        return self._clics, 42

    def accuser_clic(self, clic, confirmation, message=True):
        self.accuses.append(confirmation)


def clic(data):
    return {"id": "1", "data": data, "message": {"message_id": 7, "chat": {"id": "x"}}}


def test_boutons_enregistrent_la_decision_et_le_suivi():
    journal = Journal(":memory:")
    a = journal.ouvrir_conseil("A", 100, 90, 120, 200, 2)
    b = journal.ouvrir_conseil("B", 50, 45, 60, 100, 2)
    notif = FauxNotifieur([clic(f"achete:{a}"), clic(f"ignore:{b}"), clic("achete:999")])
    assert cli.traiter_clics(journal, notif, None, 1) == 42
    assert journal.conseil(a)["decision"] == "achete"
    assert journal.conseil(b)["decision"] == "ignore"
    assert "Achat A noté" in notif.accuses[0] and "expiré" in notif.accuses[2]

    # au stop, seul le conseil acheté donne lieu à un message ; l'ignoré est clos en silence
    messages = cli.suivre_conseils(journal, {"A": serie([100, 89]), "B": serie([50, 44])})
    assert len(messages) == 1 and "Vends A" in messages[0]
    bilan = journal.bilan_conseils()
    assert bilan["clos"] == 2 and bilan["suivis"] == 1


def test_message_telegram_avec_boutons(monkeypatch):
    monkeypatch.setenv("TG_T", "jeton")
    monkeypatch.setenv("TG_C", "123")
    envois = []
    monkeypatch.setattr(Notifieur, "_poster", staticmethod(lambda url, **kw: envois.append(kw)))
    Notifieur({"telegram": {"token_env": "TG_T", "chat_id_env": "TG_C"}}).envoyer(
        "texte", boutons=[("✅ J'ai acheté", "achete:1")])
    clavier = envois[0]["json"]["reply_markup"]["inline_keyboard"]
    assert clavier == [[{"text": "✅ J'ai acheté", "callback_data": "achete:1"}]]


def test_migration_base_existante(tmp_path):
    chemin = tmp_path / "j.sqlite"
    db = sqlite3.connect(chemin)
    db.execute("""CREATE TABLE conseils (id INTEGER PRIMARY KEY AUTOINCREMENT, cree_le TEXT, ticker TEXT,
        cours REAL, stop REAL, objectif REAL, montant_eur REAL, frais_eur REAL,
        statut TEXT DEFAULT 'ouvert', clos_le TEXT, cours_final REAL)""")
    db.commit()
    db.close()
    journal = Journal(chemin)
    id_ = journal.ouvrir_conseil("A", 1, 0.9, 1.2, 10, 2)
    assert journal.decider_conseil(id_, "achete")


def test_tickers_a_suffixe_non_cliquables():
    from boursicoteur.notifications import pour_telegram

    texte = pour_telegram("🔴 Sanofi (SAN.PA) : cours < MM20 & ASML.AS\nhttps://x.com/a.PA")
    assert "(<code>SAN.PA</code>)" in texte and "<code>ASML.AS</code>" in texte
    assert "&lt; MM20 &amp;" in texte
    assert "https://x.com/a.PA" in texte  # les vrais liens restent intacts
