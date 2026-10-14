import json
from types import SimpleNamespace

from boursicoteur import hypotheses as hyp
from boursicoteur.signaux import Piste

REPONSE = {
    "contexte": "Marché calme.",
    "hypotheses": [
        {"ticker": "NVDA", "sens": "hausse", "horizon_jours": 400, "confiance": 4,
         "raisonnement": "r", "invalidation": "i"},
        {"ticker": "INCONNU", "sens": "baisse", "horizon_jours": 10, "confiance": 2,
         "raisonnement": "r", "invalidation": "i"},
    ],
    "introductions_a_suivre": [],
}


class FauxClient:
    def __init__(self, stop_reason="end_turn", texte=json.dumps(REPONSE)):
        self.appels = []
        reponse = SimpleNamespace(
            stop_reason=stop_reason,
            stop_details=None,
            content=[SimpleNamespace(type="text", text=texte)],
        )
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._creer))
        self._reponse = reponse

    def _creer(self, **kwargs):
        self.appels.append(kwargs)
        return self._reponse


def test_appel_claude_et_conversion():
    client = FauxClient()
    resultat = hyp.avec_claude("données", {"modele": "claude-opus-5-5", "effort": "low"}, client)
    appel = client.appels[0]
    assert appel["model"] == "claude-opus-5-5"
    assert appel["output_config"]["format"]["type"] == "json_schema"
    assert appel["fallbacks"] == "default"

    hypotheses = hyp.depuis_claude(resultat, {"NVDA"})
    assert len(hypotheses) == 1  # le ticker inconnu est écarté
    assert hypotheses[0].horizon_jours == 90  # horizon borné
    assert hypotheses[0].origine == "ia"


def test_refus_ou_ia_desactivee():
    assert hyp.avec_claude("d", {}, FauxClient(stop_reason="refusal")) is None
    assert hyp.avec_claude("d", {"activee": False}, FauxClient()) is None
    assert hyp.avec_claude("d", {}, FauxClient(texte="pas du json")) is None


def test_sans_cle_pas_d_appel(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert hyp.avec_claude("d", {"cle_env": "ANTHROPIC_API_KEY"}) is None


def test_repli_par_regles():
    pistes = [
        Piste("A", 3.0, ["+20 % sur 1 mois"], tendance="haussière"),
        Piste("B", 3.0, [], tendance="neutre"),
        Piste("C", 1.0, [], tendance="haussière"),
    ]
    assert [h.ticker for h in hyp.par_regles(pistes)] == ["A"]


def test_entete_espace_de_travail(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_WORKSPACE_ID", raising=False)
    assert hyp.entetes({}) == {}
    monkeypatch.setenv("ANTHROPIC_WORKSPACE_ID", " wrkspc_123 ")
    assert hyp.entetes({}) == {"anthropic-workspace-id": "wrkspc_123"}
