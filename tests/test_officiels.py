from datetime import date

from boursicoteur import cli, officiels as off, utilisateurs as usr
from conftest import serie
from test_boutons import CONF, FauxNotifieur, journal_avec_admin

AUJOURD_HUI = date(2026, 10, 15)
LABOS = {"sanofi": "SAN.PA", "eli lilly": "LLY"}


def test_amf_garde_le_francais_et_ecarte_le_bruit():
    appels = []

    def get(url, params=None, timeout=30):
        appels.append(params)
        return {"results": [
            {"uin_idt_uin": "1", "identificationsociete_iso_cd_isi": "FR0000120578", "language": "fr",
             "info_titre": "Sanofi : chiffre d'affaires du troisième trimestre", "url_doc": "https://x/1.pdf"},
            {"uin_idt_uin": "2", "identificationsociete_iso_cd_isi": "FR0000120578", "language": "en",
             "info_titre": "Sanofi Q3 sales", "url_doc": "https://x/2.pdf"},
            {"uin_idt_uin": "3", "identificationsociete_iso_cd_isi": "FR0000120578", "language": "fr",
             "info_titre": "Déclaration des transactions sur actions propres", "url_doc": "https://x/3.pdf"},
            {"uin_idt_uin": "4", "identificationsociete_iso_cd_isi": "FR0000000000", "info_titre": "Autre"},
        ]}

    faits = off.amf({"FR0000120578": "SAN.PA"}, date(2026, 10, 14), get=get)
    assert [(f.ticker, f.titre, f.lien, f.cle) for f in faits] == [
        ("SAN.PA", "Sanofi : chiffre d'affaires du troisième trimestre", "https://x/1.pdf", "amf:1")]
    assert "uin_dat_amf >= date'2026-10-14'" in appels[0]["where"]
    assert 'identificationsociete_iso_cd_isi="FR0000120578"' in appels[0]["where"]


def test_ema_avis_recents_des_laboratoires_suivis():
    medicaments = {"data": [
        {"category": "Human", "name_of_medicine": "Dupixent", "active_substance": "dupilumab",
         "marketing_authorisation_developer_applicant_holder": "Sanofi Winthrop Industrie",
         "opinion_status": "Positive", "opinion_adopted_date": "09/10/2026", "ema_product_number": "EMEA/H/C/1",
         "medicine_url": "https://ema/dupixent"},
        {"category": "Human", "name_of_medicine": "Ancien", "opinion_adopted_date": "01/01/2020",
         "marketing_authorisation_developer_applicant_holder": "Sanofi Pasteur"},
        {"category": "Human", "name_of_medicine": "Autre", "opinion_adopted_date": "10/10/2026",
         "marketing_authorisation_developer_applicant_holder": "Pfizer Europe"},
    ]}
    (fait,) = off.ema(LABOS, AUJOURD_HUI, get=lambda url, params=None, timeout=30: medicaments)
    assert fait.ticker == "SAN.PA" and "Dupixent (dupilumab), avis positive du comité le 09/10/2026" in fait.titre


def test_fda_nouveautes_seulement():
    reponse = {"results": [{
        "sponsor_name": "ELI LILLY AND CO", "application_number": "NDA215866",
        "products": [{"brand_name": "ZEPBOUND"}],
        "submissions": [
            {"submission_type": "SUPPL", "submission_number": "9", "submission_status": "AP",
             "submission_status_date": "20261010", "submission_class_code": "EFFICACY"},
            {"submission_type": "SUPPL", "submission_number": "10", "submission_status": "AP",
             "submission_status_date": "20261011", "submission_class_code": "LABELING"},
        ]}]}
    (fait,) = off.fda(LABOS, AUJOURD_HUI, get=lambda url, params=None, timeout=30: reponse)
    assert fait.ticker == "LLY" and fait.titre == "FDA : ZEPBOUND, nouvelle indication autorisée le 10/10/2026."
    assert fait.lien.endswith("ApplNo=215866")


def test_essais_termines_ou_avec_resultats():
    def get(url, params=None, timeout=30):
        if params["query.spons"] != "sanofi":
            return {"studies": []}
        return {"studies": [
            {"protocolSection": {"identificationModule": {"nctId": "NCT1", "briefTitle": "Amlitelimab in eczema"},
                                 "statusModule": {"overallStatus": "COMPLETED"},
                                 "sponsorCollaboratorsModule": {"leadSponsor": {"name": "Sanofi"}}},
             "hasResults": False},
            {"protocolSection": {"identificationModule": {"nctId": "NCT2"},
                                 "statusModule": {"overallStatus": "RECRUITING"},
                                 "sponsorCollaboratorsModule": {"leadSponsor": {"name": "Sanofi"}}}},
        ]}

    (fait,) = off.essais(LABOS, AUJOURD_HUI, get=get)
    assert fait.titre == "Phase 3 : Amlitelimab in eczema (essai terminé)." and fait.cle == "essai:NCT1:COMPLETED:False"


def test_premier_passage_silencieux_puis_alerte_aux_interesses(monkeypatch):
    journal = journal_avec_admin([{"ticker": "SAN.PA", "quantite": 1, "prix_revient": 90}])
    journal.enregistrer_utilisateur({"chat_id": "b", "nom": "Bob", "statut": usr.ACTIF, "themes": []})
    publications = [off.Fait("AMF", "SAN.PA", "Ancien communiqué", "https://x/0", "communiqué officiel", "amf:0")]
    monkeypatch.setattr(cli.off, "amf", lambda isins, depuis: list(publications))
    for source in ("ema", "fda", "essais"):
        monkeypatch.setattr(cli.off, source, lambda labos: [])
    monkeypatch.setattr(cli.src_cours, "historiques", lambda tickers, periode: {t: serie([100.0]) for t in tickers})
    notif = FauxNotifieur([])
    cli.officiels(CONF, journal, notif)
    assert notif.envois == []  # l'historique n'est pas envoyé

    publications.append(off.Fait("AMF", "SAN.PA", "Résultats", "https://x/1", "communiqué officiel", "amf:1"))
    cli.officiels(CONF, journal, notif, {})
    assert notif.destinataires == ["x"]  # Bob ne suit ni Sanofi ni aucun domaine
    assert "🏛️ AMF · Sanofi (SAN.PA)\nRésultats" in notif.envois[0][1]
    assert journal.effet_par_categorie() == {}  # noté, mesuré plus tard
    assert journal.db.execute("SELECT categories FROM catalyseurs").fetchone()[0] == "communiqué officiel"
