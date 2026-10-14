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
CREATE TABLE IF NOT EXISTS cours_vus (
    ticker TEXT PRIMARY KEY,
    cours REAL NOT NULL,
    vu_le TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conseils (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cree_le TEXT NOT NULL,
    ticker TEXT NOT NULL,
    cours REAL NOT NULL,
    stop REAL NOT NULL,
    objectif REAL NOT NULL,
    montant_eur REAL NOT NULL,
    frais_eur REAL NOT NULL,
    statut TEXT NOT NULL DEFAULT 'ouvert',
    clos_le TEXT,
    cours_final REAL
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

    # --- cours ---------------------------------------------------------------
    def echanger_cours(
        self, cours: dict[str, float], age_max: timedelta = timedelta(hours=3),
        maintenant: datetime | None = None,
    ) -> dict[str, float]:
        """Enregistre les cours du contrôle et renvoie ceux du contrôle précédent,
        s'il date de moins de ``age_max`` (au-delà, l'écart mesurerait un trou
        de cotation de la nuit, déjà couvert par l'alerte de séance)."""
        maintenant = maintenant or datetime.now()
        precedents = {}
        for ticker, valeur in cours.items():
            ligne = self.db.execute("SELECT cours, vu_le FROM cours_vus WHERE ticker = ?",
                                    (ticker,)).fetchone()
            if ligne and maintenant - datetime.fromisoformat(ligne["vu_le"]) <= age_max:
                precedents[ticker] = ligne["cours"]
            self.db.execute(
                "INSERT OR REPLACE INTO cours_vus (ticker, cours, vu_le) VALUES (?, ?, ?)",
                (ticker, valeur, maintenant.isoformat(timespec="seconds")),
            )
        self.db.commit()
        return precedents

    # --- conseils d'achat (phase 2) -------------------------------------------
    def ouvrir_conseil(self, ticker: str, cours: float, stop: float, objectif: float,
                       montant_eur: float, frais_eur: float, le: datetime | None = None) -> int | None:
        """Note un conseil d'achat ; None s'il y en a déjà un en cours sur ce ticker."""
        if self.db.execute("SELECT 1 FROM conseils WHERE ticker = ? AND statut = 'ouvert'",
                           (ticker,)).fetchone():
            return None
        cur = self.db.execute(
            """INSERT INTO conseils (cree_le, ticker, cours, stop, objectif, montant_eur, frais_eur)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ((le or datetime.now()).isoformat(timespec="seconds"), ticker, cours, stop, objectif,
             montant_eur, frais_eur),
        )
        self.db.commit()
        return int(cur.lastrowid)

    def conseils_ouverts(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM conseils WHERE statut = 'ouvert'").fetchall()

    def clore_conseil(self, id_: int, statut: str, cours_final: float, le: date | None = None) -> None:
        self.db.execute(
            "UPDATE conseils SET statut = ?, cours_final = ?, clos_le = ? WHERE id = ?",
            (statut, cours_final, (le or date.today()).isoformat(), id_),
        )
        self.db.commit()

    def bilan_conseils(self) -> dict[str, float | int | None]:
        """Résultat des conseils clos, comme si chacun avait été suivi à la lettre."""
        ouverts = len(self.conseils_ouverts())
        lignes = self.db.execute("SELECT * FROM conseils WHERE statut != 'ouvert'").fetchall()
        if not lignes:
            return {"clos": 0, "ouverts": ouverts, "gagnants_pct": None, "gain_net_eur": None}
        gains = [l["montant_eur"] * (l["cours_final"] / l["cours"] - 1) - l["frais_eur"] for l in lignes]
        return {
            "clos": len(lignes),
            "ouverts": ouverts,
            "gagnants_pct": 100 * sum(g > 0 for g in gains) / len(gains),
            "gain_net_eur": sum(gains),
        }

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
