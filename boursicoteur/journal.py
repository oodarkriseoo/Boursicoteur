"""Journal SQLite : actus déjà vues, alertes envoyées, hypothèses et leur bilan.

Le journal est ce qui rend l'outil honnête : chaque hypothèse est notée avec
le cours du moment, puis évaluée à l'échéance. Le bilan compare le taux de
réussite à la simple détention de l'ETF de référence.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS actus_vues (
    lien TEXT PRIMARY KEY,
    vue_le TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alertes (
    cle TEXT NOT NULL,
    jour TEXT NOT NULL,
    message TEXT NOT NULL,
    PRIMARY KEY (cle, jour)
);
CREATE TABLE IF NOT EXISTS hypotheses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    creee_le TEXT NOT NULL,
    ticker TEXT NOT NULL,
    sens TEXT NOT NULL CHECK (sens IN ('hausse', 'baisse')),
    horizon_jours INTEGER NOT NULL,
    confiance INTEGER NOT NULL,
    raisonnement TEXT NOT NULL,
    invalidation TEXT NOT NULL,
    origine TEXT NOT NULL,
    cours_initial REAL,
    reference_initiale REAL,
    evaluee_le TEXT,
    cours_final REAL,
    reference_finale REAL,
    reussie INTEGER
);
"""


@dataclass
class Hypothese:
    ticker: str
    sens: str  # « hausse » ou « baisse »
    horizon_jours: int
    confiance: int  # 1 à 5
    raisonnement: str
    invalidation: str
    origine: str = "regles"  # « ia » ou « regles »


class Journal:
    def __init__(self, chemin: str | Path):
        chemin = Path(chemin)
        if str(chemin) != ":memory:":
            chemin.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(chemin))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # --- actus -------------------------------------------------------------
    def nouvelles_actus(self, liens: list[str]) -> set[str]:
        """Enregistre les liens et renvoie ceux qui n'avaient jamais été vus."""
        nouveaux = set()
        maintenant = datetime.now().isoformat(timespec="seconds")
        for lien in liens:
            cur = self.db.execute(
                "INSERT OR IGNORE INTO actus_vues (lien, vue_le) VALUES (?, ?)", (lien, maintenant)
            )
            if cur.rowcount:
                nouveaux.add(lien)
        self.db.commit()
        return nouveaux

    # --- alertes -----------------------------------------------------------
    def alerte_inedite(self, cle: str, message: str, jour: date | None = None) -> bool:
        """True si l'alerte n'a pas encore été envoyée aujourd'hui (et la note)."""
        jour = (jour or date.today()).isoformat()
        cur = self.db.execute(
            "INSERT OR IGNORE INTO alertes (cle, jour, message) VALUES (?, ?, ?)",
            (cle, jour, message),
        )
        self.db.commit()
        return bool(cur.rowcount)

    # --- hypothèses --------------------------------------------------------
    def noter(
        self,
        hypothese: Hypothese,
        cours: float | None,
        reference: float | None,
        le: datetime | None = None,
    ) -> int:
        cur = self.db.execute(
            """INSERT INTO hypotheses (creee_le, ticker, sens, horizon_jours, confiance,
                   raisonnement, invalidation, origine, cours_initial, reference_initiale)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                (le or datetime.now()).isoformat(timespec="seconds"),
                hypothese.ticker,
                hypothese.sens,
                hypothese.horizon_jours,
                hypothese.confiance,
                hypothese.raisonnement,
                hypothese.invalidation,
                hypothese.origine,
                cours,
                reference,
            ),
        )
        self.db.commit()
        return int(cur.lastrowid)

    def a_evaluer(self, aujourd_hui: date | None = None) -> list[sqlite3.Row]:
        aujourd_hui = aujourd_hui or date.today()
        lignes = self.db.execute(
            "SELECT * FROM hypotheses WHERE evaluee_le IS NULL AND cours_initial IS NOT NULL"
        ).fetchall()
        return [
            l
            for l in lignes
            if datetime.fromisoformat(l["creee_le"]).date() + timedelta(days=l["horizon_jours"])
            <= aujourd_hui
        ]

    def evaluer(
        self, id_: int, cours_final: float, reference_finale: float | None, le: date | None = None
    ) -> bool:
        ligne = self.db.execute("SELECT * FROM hypotheses WHERE id = ?", (id_,)).fetchone()
        variation = cours_final / ligne["cours_initial"] - 1
        reussie = variation > 0 if ligne["sens"] == "hausse" else variation < 0
        self.db.execute(
            """UPDATE hypotheses SET evaluee_le = ?, cours_final = ?, reference_finale = ?,
                   reussie = ? WHERE id = ?""",
            ((le or date.today()).isoformat(), cours_final, reference_finale, int(reussie), id_),
        )
        self.db.commit()
        return reussie

    def bilan(self) -> dict[str, float | int | None]:
        """Statistiques sur les hypothèses évaluées.

        ``gain_moyen_pct`` : ce qu'aurait rapporté en moyenne le fait de suivre
        chaque hypothèse (achat si hausse, vente si baisse), hors frais.
        ``reference_moyenne_pct`` : ce qu'a fait l'ETF de référence sur les
        mêmes périodes. L'outil n'apporte quelque chose que si le premier
        dépasse nettement le second, frais compris.
        """
        lignes = self.db.execute("SELECT * FROM hypotheses WHERE evaluee_le IS NOT NULL").fetchall()
        if not lignes:
            return {"evaluees": 0, "taux_reussite_pct": None, "gain_moyen_pct": None,
                    "reference_moyenne_pct": None}
        gains, refs = [], []
        for l in lignes:
            variation = (l["cours_final"] / l["cours_initial"] - 1) * 100
            gains.append(variation if l["sens"] == "hausse" else -variation)
            if l["reference_initiale"] and l["reference_finale"]:
                refs.append((l["reference_finale"] / l["reference_initiale"] - 1) * 100)
        return {
            "evaluees": len(lignes),
            "taux_reussite_pct": 100 * sum(l["reussie"] for l in lignes) / len(lignes),
            "gain_moyen_pct": sum(gains) / len(gains),
            "reference_moyenne_pct": sum(refs) / len(refs) if refs else None,
        }
