from boursicoteur.notifications import decouper
from boursicoteur.sources.introductions import lire_nasdaq, lire_sec

NASDAQ = {
    "data": {
        "upcoming": {
            "upcomingTable": {
                "rows": [
                    {"companyName": "Acme AI Inc.", "proposedTickerSymbol": "ACAI",
                     "proposedExchange": "NASDAQ Global", "proposedSharePrice": "14.00-16.00",
                     "expectedPriceDate": "10/20/2026", "dollarValueOfSharesOffered": "$150,000,000"}
                ]
            }
        },
        "priced": {"rows": [{"companyName": "Old Co", "proposedTickerSymbol": "OLD",
                             "pricedDate": "10/01/2026"}]},
        "filed": {"rows": []},
    }
}


def test_lire_nasdaq():
    intros = lire_nasdaq(NASDAQ)
    assert [(i.societe, i.statut, i.ticker) for i in intros] == [
        ("Acme AI Inc.", "à venir", "ACAI"),
        ("Old Co", "cotée", "OLD"),
    ]
    assert lire_nasdaq({}) == []
    assert lire_nasdaq({"data": None}) == []


def test_lire_sec_ignore_les_amendements():
    entrees = [
        {"title": "S-1 - Tiny Robotics Corp (0001234567) (Filer)", "updated": "2026-10-13T16:00:00-04:00",
         "link": "https://sec.gov/x"},
        {"title": "S-1/A - Other Corp (0007654321) (Filer)"},
    ]
    intros = lire_sec(entrees, "S-1")
    assert [(i.societe, i.date) for i in intros] == [("Tiny Robotics Corp", "2026-10-13")]


def test_decouper_respecte_la_limite():
    texte = "\n".join(["ligne " * 10] * 50)
    morceaux = decouper(texte, 200)
    assert all(len(m) <= 200 for m in morceaux)
    assert "".join(morceaux) == texte
