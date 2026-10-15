"""Journal SQLite : actus déjà vues, alertes envoyées, hypothèses et leur bilan.

Le journal est ce qui rend l'outil honnête : chaque hypothèse est notée avec
le cours du moment, puis évaluée à l'échéance. Le bilan compare le taux de
réussite à la simple détention de l'ETF de référence.
"""

from __future__ import annotations

import json
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
    cours_final REAL,
    decision TEXT,  -- achete, ignore ou ibkr (ordre passé par le bot), sinon inconnue
    decide_le TEXT,
    quantite REAL,
    chat_id TEXT NOT NULL DEFAULT ''  -- utilisateur à qui le conseil a été donné
);
CREATE TABLE IF NOT EXISTS catalyseurs (
    lien TEXT PRIMARY KEY,
    ticker TEXT NOT NULL,
    categories TEXT NOT NULL,  -- ex. « contrat,partenariat » ou « négatif »
    titre TEXT NOT NULL,
    vu_le TEXT NOT NULL,
    cours REAL NOT NULL,
    reference REAL,
    ecart_1j REAL,  -- variation du titre moins celle de la référence, en points
    ecart_1s REAL,
    ecart_4s REAL
);
CREATE TABLE IF NOT EXISTS valeurs_portefeuille (
    chat_id TEXT NOT NULL DEFAULT '',
    jour TEXT NOT NULL,  -- une photo par jour, la dernière de la journée l'emporte
    valeur_eur REAL NOT NULL,
    apport_eur REAL NOT NULL,  -- argent ajouté (+) ou retiré (−) depuis la photo de la veille
    reference REAL NOT NULL,  -- cours de l'ETF de référence au même moment
    lignes TEXT NOT NULL,  -- JSON ticker → [quantité, prix en €, coté ?]
    PRIMARY KEY (chat_id, jour)
);
CREATE TABLE IF NOT EXISTS utilisateurs (
    chat_id TEXT PRIMARY KEY,  -- conversation Telegram
    nom TEXT NOT NULL,
    statut TEXT NOT NULL,  -- admin, actif, en_attente ou refuse
    cree_le TEXT NOT NULL,
    portefeuille TEXT NOT NULL DEFAULT '[]',  -- JSON, mêmes champs que dans config.yaml
    suivis TEXT NOT NULL DEFAULT '[]',  -- JSON, tickers surveillés sans être détenus
    poche_eur REAL,  -- poche des conseils d'achat ; vide = celle de config.yaml
    themes TEXT  -- JSON, domaines suivis parmi les thèmes de config.yaml ; vide = tous
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
        # bases créées avant les boutons Telegram
        colonnes = {l["name"] for l in self.db.execute("PRAGMA table_info(conseils)")}
        for colonne, type_ in (("decision", "TEXT"), ("decide_le", "TEXT"), ("quantite", "REAL"),
                               ("chat_id", "TEXT NOT NULL DEFAULT ''")):
            if colonne not in colonnes:
                self.db.execute(f"ALTER TABLE conseils ADD COLUMN {colonne} {type_}")
        if "themes" not in {l["name"] for l in self.db.execute("PRAGMA table_info(utilisateurs)")}:
            self.db.execute("ALTER TABLE utilisateurs ADD COLUMN themes TEXT")
        # photos prises avant le passage à plusieurs utilisateurs : une seule par jour
        if "chat_id" not in {l["name"] for l in self.db.execute("PRAGMA table_info(valeurs_portefeuille)")}:
            self.db.executescript(
                "ALTER TABLE valeurs_portefeuille RENAME TO valeurs_portefeuille_avant;" + SCHEMA +
                "INSERT INTO valeurs_portefeuille (jour, valeur_eur, apport_eur, reference, lignes) "
                "SELECT jour, valeur_eur, apport_eur, reference, lignes FROM valeurs_portefeuille_avant;"
                "DROP TABLE valeurs_portefeuille_avant;")
        self.db.commit()

    # --- utilisateurs --------------------------------------------------------
    def utilisateur(self, chat_id: str) -> dict | None:
        ligne = self.db.execute("SELECT * FROM utilisateurs WHERE chat_id = ?", (str(chat_id),)).fetchone()
        if ligne is None:
            return None
        return {**dict(ligne), "portefeuille": json.loads(ligne["portefeuille"]),
                "suivis": json.loads(ligne["suivis"]),
                "themes": json.loads(ligne["themes"]) if ligne["themes"] is not None else None}

    def utilisateurs(self, *statuts: str) -> list[dict]:
        lignes = self.db.execute("SELECT chat_id FROM utilisateurs ORDER BY cree_le").fetchall()
        tous = [self.utilisateur(l["chat_id"]) for l in lignes]
        return [u for u in tous if not statuts or u["statut"] in statuts]

    def enregistrer_utilisateur(self, u: dict) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO utilisateurs (chat_id, nom, statut, cree_le, portefeuille, suivis, poche_eur, "
            "themes) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (str(u["chat_id"]), u["nom"], u["statut"], u.get("cree_le") or datetime.now().isoformat(timespec="seconds"),
             json.dumps(u.get("portefeuille") or [], ensure_ascii=False), json.dumps(u.get("suivis") or []),
             u.get("poche_eur"), json.dumps(u["themes"]) if u.get("themes") is not None else None),
        )
        self.db.commit()

    def oublier_utilisateur(self, chat_id: str) -> None:
        """Efface tout ce qui appartient à cet utilisateur : réglages, conseils, photos."""
        for table in ("utilisateurs", "conseils", "valeurs_portefeuille"):
            self.db.execute(f"DELETE FROM {table} WHERE chat_id = ?", (str(chat_id),))
        self.db.execute("DELETE FROM alertes WHERE cle LIKE ?", (f"{chat_id}:%",))
        self.db.commit()

    def attribuer(self, chat_id: str) -> None:
        """Donne à ``chat_id`` les conseils et photos d'avant les utilisateurs (sans propriétaire)."""
        for table in ("conseils", "valeurs_portefeuille"):
            self.db.execute(f"UPDATE {table} SET chat_id = ? WHERE chat_id = ''", (str(chat_id),))
        self.db.commit()

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

    def liens_inconnus(self, liens: list[str]) -> set[str]:
        """Comme ``nouvelles_actus``, sans rien enregistrer."""
        return {l for l in liens
                if self.db.execute("SELECT 1 FROM actus_vues WHERE lien = ?", (l,)).fetchone() is None}

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

    # --- valeur du portefeuille ------------------------------------------------
    def photographier(self, jour: date, valeur_eur: float, apport_eur: float, reference: float,
                      lignes: dict[str, list], chat_id: str = "") -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO valeurs_portefeuille (chat_id, jour, valeur_eur, apport_eur, reference, lignes) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (str(chat_id), jour.isoformat(), valeur_eur, apport_eur, reference, json.dumps(lignes)),
        )
        self.db.commit()

    def photo_avant(self, jour: date, chat_id: str = "") -> dict | None:
        """Dernière photo d'un jour antérieur, lignes décodées."""
        ligne = self.db.execute("SELECT * FROM valeurs_portefeuille WHERE chat_id = ? AND jour < ? "
                                "ORDER BY jour DESC LIMIT 1", (str(chat_id), jour.isoformat())).fetchone()
        return {**dict(ligne), "lignes": json.loads(ligne["lignes"])} if ligne else None

    def photos(self, chat_id: str = "") -> list[sqlite3.Row]:
        return self.db.execute("SELECT jour, valeur_eur, apport_eur, reference FROM valeurs_portefeuille "
                               "WHERE chat_id = ? ORDER BY jour", (str(chat_id),)).fetchall()

    # --- conseils d'achat (phase 2) -------------------------------------------
    def ouvrir_conseil(self, ticker: str, cours: float, stop: float, objectif: float,
                       montant_eur: float, frais_eur: float, le: datetime | None = None,
                       quantite: float | None = None, chat_id: str = "") -> int | None:
        """Note un conseil d'achat ; None si cet utilisateur en a déjà un en cours sur ce ticker."""
        if self.db.execute("SELECT 1 FROM conseils WHERE ticker = ? AND statut = 'ouvert' AND chat_id = ?",
                           (ticker, str(chat_id))).fetchone():
            return None
        cur = self.db.execute(
            """INSERT INTO conseils (cree_le, ticker, cours, stop, objectif, montant_eur, frais_eur,
                   quantite, chat_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ((le or datetime.now()).isoformat(timespec="seconds"), ticker, cours, stop, objectif,
             montant_eur, frais_eur, quantite, str(chat_id)),
        )
        self.db.commit()
        return int(cur.lastrowid)

    def conseils_ouverts(self, chat_id: str | None = None) -> list[sqlite3.Row]:
        """Conseils en cours d'un utilisateur, ou de tous si ``chat_id`` est None."""
        if chat_id is None:
            return self.db.execute("SELECT * FROM conseils WHERE statut = 'ouvert'").fetchall()
        return self.db.execute("SELECT * FROM conseils WHERE statut = 'ouvert' AND chat_id = ?",
                               (str(chat_id),)).fetchall()

    def clore_conseil(self, id_: int, statut: str, cours_final: float, le: date | None = None) -> None:
        self.db.execute(
            "UPDATE conseils SET statut = ?, cours_final = ?, clos_le = ? WHERE id = ?",
            (statut, cours_final, (le or date.today()).isoformat(), id_),
        )
        self.db.commit()

    def conseil(self, id_: int) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM conseils WHERE id = ?", (id_,)).fetchone()

    def decider_conseil(self, id_: int, decision: str, le: date | None = None) -> bool:
        cur = self.db.execute("UPDATE conseils SET decision = ?, decide_le = ? WHERE id = ?",
                              (decision, (le or date.today()).isoformat(), id_))
        self.db.commit()
        return bool(cur.rowcount)

    def engage_ibkr(self, jour: date | None = None) -> float:
        """Montant (en euros) des ordres passés par le bot ce jour-là, pour le plafond quotidien."""
        ligne = self.db.execute(
            "SELECT COALESCE(SUM(montant_eur), 0) FROM conseils WHERE decision = 'ibkr' AND decide_le = ?",
            ((jour or date.today()).isoformat(),),
        ).fetchone()
        return float(ligne[0])

    def bilan_conseils(self, chat_id: str | None = None) -> dict[str, float | int | None]:
        """Résultat des conseils clos, comme si chacun avait été suivi à la lettre."""
        ouverts = len(self.conseils_ouverts(chat_id))
        lignes = self.db.execute(
            "SELECT * FROM conseils WHERE statut != 'ouvert'" + (" AND chat_id = ?" if chat_id is not None else ""),
            (str(chat_id),) if chat_id is not None else ()).fetchall()
        if not lignes:
            return {"clos": 0, "ouverts": ouverts, "gagnants_pct": None, "gain_net_eur": None,
                    "suivis": 0, "gain_net_suivis_eur": None}
        gains = [l["montant_eur"] * (l["cours_final"] / l["cours"] - 1) - l["frais_eur"] for l in lignes]
        suivis = [g for g, l in zip(gains, lignes) if l["decision"] in ("achete", "ibkr")]
        return {
            "clos": len(lignes),
            "ouverts": ouverts,
            "gagnants_pct": 100 * sum(g > 0 for g in gains) / len(gains),
            "gain_net_eur": sum(gains),
            "suivis": len(suivis),
            "gain_net_suivis_eur": sum(suivis) if suivis else None,
        }

    # --- historique des news et de leur effet ---------------------------------
    HORIZONS = {"ecart_1j": 1, "ecart_1s": 7, "ecart_4s": 28}  # en jours calendaires

    def noter_catalyseur(self, lien: str, ticker: str, categories: list[str], titre: str,
                         cours: float, reference: float | None, le: datetime | None = None) -> bool:
        cur = self.db.execute(
            """INSERT OR IGNORE INTO catalyseurs (lien, ticker, categories, titre, vu_le, cours, reference)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (lien, ticker, ",".join(categories), titre,
             (le or datetime.now()).isoformat(timespec="seconds"), cours, reference),
        )
        self.db.commit()
        return bool(cur.rowcount)

    def mesurer_catalyseurs(self, cours: dict[str, float], reference: float | None,
                            maintenant: datetime | None = None) -> int:
        """Remplit l'effet à 1 jour, 1 semaine et 4 semaines des news arrivées à
        échéance, avec les cours du moment. Renvoie le nombre de mesures faites."""
        maintenant = maintenant or datetime.now()
        faites = 0
        for colonne, jours in self.HORIZONS.items():
            lignes = self.db.execute(f"SELECT * FROM catalyseurs WHERE {colonne} IS NULL").fetchall()
            for l in lignes:
                if maintenant - datetime.fromisoformat(l["vu_le"]) < timedelta(days=jours):
                    continue
                actuel = cours.get(l["ticker"])
                if actuel is None:
                    continue
                ecart = (actuel / l["cours"] - 1) * 100
                if reference and l["reference"]:
                    ecart -= (reference / l["reference"] - 1) * 100
                self.db.execute(f"UPDATE catalyseurs SET {colonne} = ? WHERE lien = ?", (ecart, l["lien"]))
                faites += 1
        self.db.commit()
        return faites

    def effet_par_categorie(self, colonne: str = "ecart_1s") -> dict[str, dict[str, float]]:
        """Pour chaque catégorie de news : nombre de cas mesurés, écart moyen à la
        référence (en points) et part des cas où le titre a fait mieux qu'elle."""
        effets: dict[str, list[float]] = {}
        for l in self.db.execute(f"SELECT categories, {colonne} AS e FROM catalyseurs WHERE {colonne} IS NOT NULL"):
            for categorie in filter(None, l["categories"].split(",")):
                effets.setdefault(categorie, []).append(l["e"])
        return {
            c: {"cas": len(v), "moyenne": sum(v) / len(v), "positifs_pct": 100 * sum(e > 0 for e in v) / len(v)}
            for c, v in effets.items()
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

    def bilan(self, origine: str | None = None) -> dict[str, float | int | None]:
        """Statistiques sur les hypothèses évaluées.

        ``gain_moyen_pct`` : ce qu'aurait rapporté en moyenne le fait de suivre
        chaque hypothèse (achat si hausse, vente si baisse), hors frais.
        ``reference_moyenne_pct`` : ce qu'a fait l'ETF de référence sur les
        mêmes périodes. L'outil n'apporte quelque chose que si le premier
        dépasse nettement le second, frais compris.
        """
        lignes = self.db.execute(
            "SELECT * FROM hypotheses WHERE evaluee_le IS NOT NULL" + (" AND origine = ?" if origine else ""),
            (origine,) if origine else (),
        ).fetchall()
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
