import numpy as np
import pandas as pd
import pytest


def serie(cours, volumes=None):
    index = pd.bdate_range("2026-01-01", periods=len(cours))
    volumes = volumes if volumes is not None else [1_000_000] * len(cours)
    return pd.DataFrame({"Close": cours, "Volume": volumes}, index=index)


@pytest.fixture
def hausse():
    return serie(list(np.linspace(100, 160, 80)))


@pytest.fixture
def baisse():
    return serie(list(np.linspace(160, 100, 80)))


@pytest.fixture(autouse=True)
def annuaire_hors_ligne(monkeypatch):
    """Petit annuaire fixe : les tests ne téléchargent pas FinanceDatabase."""
    from boursicoteur import annuaire

    base = pd.DataFrame(
        [["Sanofi", "Health Care", "Health Care", "Pharmaceuticals", "PAR", "France", "FR0000120578", "Large Cap", False],
         ["Safran SA", "Industrials", "Capital Goods", "Aerospace & Defense", "PAR", "France", "FR0000073272",
          "Large Cap", False],
         ["Safran SA", "Industrials", "Capital Goods", "Aerospace & Defense", "FRA", "France", "FR0000073272",
          "Large Cap", False],
         ["Thales S.A.", "Industrials", "Capital Goods", "Aerospace & Defense", "PAR", "France", "FR0000121329",
          "Large Cap", False],
         ["Eli Lilly and Company", "Health Care", "Health Care", "Pharmaceuticals", "NYQ", "United States",
          "US5324571083", "Mega Cap", False]],
        index=pd.Index(["SAN.PA", "SAF.PA", "SEJ1.F", "HO.PA", "LLY"], name="symbol"),
        columns=["name", "sector", "industry_group", "industry", "exchange", "country", "isin", "market_cap",
                 "delisted"])
    monkeypatch.setattr(annuaire, "_base", base)
