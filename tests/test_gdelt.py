from datetime import datetime, timedelta, timezone

from boursicoteur import cli, config as cfg
from boursicoteur.sources import gdelt
from test_boutons import journal_avec_admin

MAINTENANT = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)
REGLAGES = {**cfg.DEFAUTS["gdelt"], "par_passage": 2}


def courbe(habituel, recent):
    """Trois jours par quart d'heure : ``habituel`` articles, puis ``recent`` sur les 4 dernières heures."""
    points = []
    for i in range(72 * 4):
        moment = MAINTENANT - timedelta(minutes=15 * (72 * 4 - i))
        points.append((moment, recent if moment >= MAINTENANT - timedelta(hours=4) else habituel))
    return points


def test_pic_compare_aux_jours_precedents():
    assert gdelt.pic(courbe(2, 2), 4, 3, 30, MAINTENANT) is None  # sujet habituel
    recent, multiple = gdelt.pic(courbe(2, 8), 4, 3, 30, MAINTENANT)
    assert recent == 8 * 16 and round(multiple, 1) == 4.0
    assert gdelt.pic(courbe(0, 1), 4, 3, 30, MAINTENANT) is None  # trop peu d'articles
    assert gdelt.pic([], 4, 3, 30, MAINTENANT) is None


def test_reponse_gdelt_lue(monkeypatch):
    monkeypatch.setattr(gdelt, "_get", lambda params: {"timeline": [{"series": "Article Count", "data": [
        {"date": "20261021T110000Z", "value": 12}, {"date": "pas une date", "value": 3}]}]})
    assert gdelt.volumes("tariffs") == [(datetime(2026, 10, 21, 11, tzinfo=timezone.utc), 12)]


def test_sujets_lus_a_tour_de_role_et_signales_une_fois(monkeypatch):
    lus = []

    def faux(params):
        lus.append((params["mode"], params["query"]))
        if params["query"] != "hormuz":
            return {"timeline": [{"data": []}]}
        if params["mode"] == "artlist":
            return {"articles": [{"url": "http://a/1", "title": "Tankers rerouted", "domain": "reuters.com"},
                                 {"url": "http://a/2", "title": "Oil jumps"}]}
        return {"timeline": [{"data": [{"date": m.strftime("%Y%m%dT%H%M%SZ"), "value": v}
                                       for m, v in courbe(2, 10)]}]}

    monkeypatch.setattr(gdelt, "_get", faux)
    monkeypatch.setattr(gdelt, "datetime", type("D", (datetime,), {"now": staticmethod(lambda tz=None: MAINTENANT)}))
    etat = {}
    (evt,) = gdelt.sujets_en_hausse(["tariffs", "hormuz", "opep"], etat, REGLAGES)
    assert "hormuz" in evt.titre and "5.0 fois l'habitude" in evt.titre
    assert evt.lien == "http://a/1" and evt.source == "GDELT, reuters.com"
    assert "Tankers rerouted | Oil jumps" in evt.resume
    assert [q for m, q in lus if m == "timelinevolraw"] == ["tariffs", "hormuz"]
    # passages suivants : opep et tariffs, puis hormuz (au repos 24 h, pas relu) et opep
    assert gdelt.sujets_en_hausse(["tariffs", "hormuz", "opep"], etat, REGLAGES) == []
    assert gdelt.sujets_en_hausse(["tariffs", "hormuz", "opep"], etat, REGLAGES) == []
    assert [q for m, q in lus if m == "timelinevolraw"][2:] == ["opep", "tariffs", "opep"]


def test_sujets_avec_le_nom_des_positions():
    journal = journal_avec_admin([{"ticker": "MC.PA", "nom": "LVMH", "quantite": 1, "prix_revient": 600},
                                  {"ticker": "BTC-EUR", "nom": "BTC", "quantite": 1, "prix_revient": 1}])
    conf = cfg._fusionner(cfg.DEFAUTS, {"gdelt": {"sujets": ["tariffs"]}})
    assert cli.sujets_gdelt(conf, journal) == ["tariffs", '"LVMH"']  # « BTC » trop court pour être sûr
