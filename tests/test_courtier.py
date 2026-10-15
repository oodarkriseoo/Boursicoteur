from types import SimpleNamespace

from boursicoteur import cli
from boursicoteur.config import DEFAUTS
from boursicoteur.courtier import Courtier, contrat_ibkr
from boursicoteur.journal import Journal
from test_boutons import CONF, FauxNotifieur, clic, journal_avec_admin

REGLAGES = {**DEFAUTS["courtier"], "type": "ibkr"}


def action(symbole, place, devise):
    return SimpleNamespace(symbol=symbole, exchange=place, currency=devise)


def courtier(ib):
    return Courtier(REGLAGES, lambda: ib, action)


class FauxIB:
    def __init__(self, comptes=("DU123",)):
        self.comptes, self.ordres, self.deconnecte = list(comptes), [], False

    def connect(self, *a, **k):
        pass

    def managedAccounts(self):
        return self.comptes

    def qualifyContracts(self, contrat):
        return [contrat]

    def bracketOrder(self, action, quantite, limite, objectif, stop, **kw):
        ordre = lambda nom, prix: SimpleNamespace(nom=nom, prix=prix, quantite=quantite, tif=kw.get("tif"))
        return SimpleNamespace(parent=ordre("achat", limite), objectif=ordre("objectif", objectif),
                               stop=ordre("stop", stop))

    def placeOrder(self, contrat, ordre):
        self.ordres.append((contrat.symbol, contrat.exchange, ordre))
        return SimpleNamespace(orderStatus=SimpleNamespace(status="Submitted"))

    def sleep(self, s):
        pass

    def disconnect(self):
        self.deconnecte = True


class Bracket(list):
    pass


def faux(comptes=("DU123",)):
    ib = FauxIB(comptes)
    original = ib.bracketOrder

    def bracket(*a, **k):
        b = original(*a, **k)
        liste = Bracket([b.parent, b.objectif, b.stop])
        liste.parent = b.parent
        return liste

    ib.bracketOrder = bracket
    return ib


def test_contrats():
    assert contrat_ibkr("NVDA") == ("NVDA", "SMART", "USD")
    assert contrat_ibkr("SOI.PA") == ("SOI", "SBF", "EUR")
    assert contrat_ibkr("BTC-EUR") is None and contrat_ibkr("X.HK") is None


def test_garde_fous():
    c = Courtier(REGLAGES)
    assert "entier" in c.verifier("NVDA", 1.5, 100, 0)
    assert "plafond par ordre" in c.verifier("NVDA", 2, 400, 0)
    assert "déjà engagés" in c.verifier("NVDA", 2, 200, 500)
    assert c.verifier("NVDA", 2, 200, 0) is None
    assert "Aucun courtier" in Courtier(DEFAUTS["courtier"]).verifier("NVDA", 2, 200, 0)


def test_ordre_bracket_sur_compte_demo():
    ib = faux()
    r = courtier(ib).acheter("NVDA", 3, 101, 120, 90, 250, 0)
    assert r.ok and "compte démo" in r.message
    assert [o[2].nom for o in ib.ordres] == ["achat", "objectif", "stop"]
    assert ib.ordres[0][2].tif == "DAY" and ib.ordres[2][2].tif == "GTC"
    assert ib.deconnecte


def test_compte_reel_refuse_tant_que_demo():
    ib = faux(("U999",))
    r = courtier(ib).acheter("NVDA", 3, 101, 120, 90, 250, 0)
    assert not r.ok and "Compte réel" in r.message and ib.ordres == []


class NotifieurQuiEnvoie(FauxNotifieur):
    def __init__(self, clics):
        super().__init__(clics)
        self.envois = []

    def envoyer(self, texte, titre="", urgent=False, boutons=None, chat_id=None):
        self.envois.append((texte, boutons))


def test_parcours_acheter_puis_confirmer():
    journal = journal_avec_admin()
    id_ = journal.ouvrir_conseil("NVDA", 100, 92, 116, 200, 2, quantite=2, chat_id="x")
    ib = faux()
    branche = courtier(ib)

    notif = NotifieurQuiEnvoie([clic(f"commande:{id_}")])
    cli.traiter_mises_a_jour(CONF, journal, notif, None, 1, branche)
    texte, boutons = notif.envois[0]
    assert "Acheter 2 NVDA via IBKR" in texte and boutons[0] == ("✔️ Confirmer", f"confirme:{id_}")
    assert ib.ordres == []  # rien ne part sans confirmation

    notif = NotifieurQuiEnvoie([clic(f"confirme:{id_}")])
    cli.traiter_mises_a_jour(CONF, journal, notif, None, 1, branche)
    assert len(ib.ordres) == 3 and "Ordre envoyé" in notif.accuses[0]
    assert journal.conseil(id_)["decision"] == "ibkr"
    assert journal.engage_ibkr() == 200

    # un second clic ne repasse pas l'ordre
    notif = NotifieurQuiEnvoie([clic(f"confirme:{id_}")])
    cli.traiter_mises_a_jour(CONF, journal, notif, None, 1, branche)
    assert len(ib.ordres) == 3 and "déjà traité" in notif.accuses[0]
