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
