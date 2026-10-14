import pytest

from boursicoteur import cli, config as cfg, evenements as evt
from boursicoteur.journal import Hypothese, Journal
from boursicoteur.sources.actus import Actu
from conftest import serie

ACTUS = [Actu("Strait of Hormuz closed to tankers", "http://e/1", "BBC"),
         Actu("Heatwave strains European power grids", "http://e/2", "BBC")]
VALEURS = {"SU.PA": "Électrification, ta position", "LLY": "Santé"}


class Notif:
    def __init__(self):
        self.envois = []

    def envoyer(self, texte, titre="", urgent=False, boutons=None):
        self.envois.append((titre, texte, boutons))


def effet(evenement=1, ticker="SU.PA", confiance=3, delai=200):
    return {"evenement": evenement, "ticker": ticker, "sens": "hausse", "rang": "indirect",
            "chaine": "Canicule, donc réseaux saturés, donc commandes d'équipements.",
            "delai_jours": delai, "confiance": confiance, "invalidation": "Pas de commande d'ici 2 mois."}


def test_preparer_numerote_les_evenements_et_liste_les_valeurs():
    texte = evt.preparer(ACTUS, VALEURS, "## Mémoire")
    assert "0. Strait of Hormuz closed to tankers (BBC)" in texte
    assert "- SU.PA : Électrification, ta position" in texte
    assert texte.endswith("## Mémoire")


def test_lire_reponse_ecarte_les_liens_inconnus_et_borne_le_delai():
    effets = evt.lire_reponse({"effets": [effet(), effet(evenement=5), effet(ticker="NVDA"),
                                          effet(ticker="LLY", confiance=4)]}, ACTUS, VALEURS)
    assert [e.ticker for e in effets] == ["LLY", "SU.PA"]  # trié par confiance
    assert effets[1].actu.lien == "http://e/2"
    assert effets[1].delai_jours == 90
    assert effets[1].categorie == "événement indirect, hausse attendue"
    assert evt.lire_reponse(None, ACTUS, VALEURS) == []


def test_formater_donne_la_chaine_et_l_enveloppe():
    e = evt.lire_reponse({"effets": [effet()]}, ACTUS, VALEURS)[0]
    texte = evt.formater(e)
    assert texte.startswith("🔗 SU.PA (Électrification, ta position · PEA possible) : ⬆️ hausse attendue")
    assert "Chaîne : Canicule" in texte and "http://e/2" in texte


@pytest.fixture
def conf():
    return cfg._fusionner(cfg.DEFAUTS, {
        "portefeuille": [{"ticker": "SU.PA"}],
        "themes": {"sante": {"libelle": "Santé", "univers": ["LLY"]}},
        "evenements": {"flux": ["http://flux"]},
    })


@pytest.fixture
def branche(monkeypatch):
    appels = []
    reponse = {"effets": [effet(), effet(evenement=0, ticker="LLY", confiance=2)]}
    monkeypatch.setattr(cli.src_actus, "lire_flux", lambda url, limite, cache: list(ACTUS))
    monkeypatch.setattr(cli.src_cours, "historiques",
                        lambda tickers, periode: {t: serie([100.0 + (i % 3) for i in range(40)])
                                                  for t in tickers})

    def claude(donnees, config_ia, schema, consignes, modele):
        appels.append(donnees)
        return reponse

    monkeypatch.setattr(cli.hyp, "avec_claude", claude)
    return appels


def test_evenements_notent_au_journal_et_alertent_au_dessus_du_seuil(conf, branche):
    journal, notif = Journal(":memory:"), Notif()
    cli.evenements(conf, journal, notif)
    assert len(branche) == 1 and "SU.PA : ta position" in branche[0]
    # les deux liens sont notés pour la mesure ; seul celui à 3/5 alerte
    lignes = journal.db.execute("SELECT lien, ticker, categories FROM catalyseurs ORDER BY ticker").fetchall()
    assert [(l["lien"], l["ticker"]) for l in lignes] == [("http://e/1#LLY", "LLY"), ("http://e/2#SU.PA", "SU.PA")]
    assert {l["origine"] for l in journal.db.execute("SELECT origine FROM hypotheses")} == {"evenement"}
    assert [t for t, _, _ in notif.envois] == ["Événement"]
    # hausse attendue : plan d'achat chiffré, suivi au journal, avec ses boutons
    titre, texte, boutons = notif.envois[0]
    assert "Conseil d'achat SU.PA" in texte
    (conseil,) = journal.conseils_ouverts()
    assert boutons == [[("✅ Déjà acheté", f"achete:{conseil['id']}"), ("❌ Ignorer", f"ignore:{conseil['id']}")]]

    # actus déjà traitées : plus d'appel à Claude
    cli.evenements(conf, journal, notif)
    assert len(branche) == 1


def test_appels_a_claude_espaces_et_actus_gardees_si_indisponible(conf, branche, monkeypatch):
    journal, etat = Journal(":memory:"), {}
    monkeypatch.setattr(cli.hyp, "avec_claude", lambda *a, **k: None)
    cli.evenements(conf, journal, Notif(), etat)
    assert journal.liens_inconnus(["evt:http://e/1"]) == {"evt:http://e/1"}  # reproposée plus tard
    assert etat["appels"] == 1
    # moins d'un quart d'heure après : pas de nouvel appel
    monkeypatch.setattr(cli.hyp, "avec_claude", lambda *a, **k: pytest.fail("appel trop tôt"))
    cli.evenements(conf, journal, Notif(), etat)


def test_baisse_sur_une_position_detenue_previent_sans_plan(conf, branche, monkeypatch):
    baisse = dict(effet(), sens="baisse")
    monkeypatch.setattr(cli.hyp, "avec_claude", lambda *a, **k: {"effets": [baisse]})
    journal, notif = Journal(":memory:"), Notif()
    cli.evenements(conf, journal, notif)
    _, texte, boutons = notif.envois[0]
    assert "Tu détiens cette valeur" in texte and boutons is None
    assert journal.conseils_ouverts() == []


def test_desactive(conf, branche):
    conf["evenements"]["actives"] = False
    cli.evenements(conf, Journal(":memory:"), Notif())
    assert branche == []


def test_bilan_par_origine():
    journal = Journal(":memory:")
    a = journal.noter(Hypothese("A", "hausse", 5, 3, "r", "i", origine="evenement"), 100, None)
    b = journal.noter(Hypothese("B", "hausse", 5, 3, "r", "i", origine="ia"), 100, None)
    journal.evaluer(a, 110, None)
    journal.evaluer(b, 90, None)
    assert journal.bilan("evenement")["taux_reussite_pct"] == 100
    assert journal.bilan("ia")["taux_reussite_pct"] == 0
    assert journal.bilan()["evaluees"] == 2


def test_flux_relu_avec_son_etag(monkeypatch):
    import feedparser

    from boursicoteur.sources import actus as src

    vus = []

    def parse(url, agent, etag=None, modified=None):
        vus.append(etag)
        return feedparser.FeedParserDict(etag="v1", entries=[], feed={})

    monkeypatch.setattr(feedparser, "parse", parse)
    cache = {}
    src.lire_flux("http://flux", cache=cache)
    src.lire_flux("http://flux", cache=cache)
    assert vus == [None, "v1"]
