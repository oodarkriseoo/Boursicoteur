# Boursicoteur

Outil de veille boursière qui tourne 24 h/24. Il suit tes positions (PEA, compte-titres), les actus et le contexte économique, repère les valeurs qui s'animent dans les thèmes suivis (IA, santé, électrification, matériaux critiques, défense, banques…), surveille les introductions en bourse, formule des hypothèses haussières ou baissières et te prévient sur Telegram ou ntfy.

**Il ne passe aucun ordre.** Il t'informe, tu décides et tu cliques dans l'appli de ton courtier (Trade Republic par exemple, qui n'a pas d'API officielle). Chaque hypothèse est notée dans un journal puis évaluée à son échéance, pour savoir honnêtement si l'outil fait mieux que de garder un ETF MSCI World.

## Ce qu'il fait

| Commande | Rôle | Fréquence conseillée |
|---|---|---|
| `veille` | Alertes de chute : séance (puis à chaque palier de 3 points), chute rapide entre deux contrôles, passage sous ton seuil de sortie (`stop`), recul depuis le plus haut, perte par rapport à ton prix de revient, VIX élevé. Chaque alerte de chute rappelle ton écart au prix de revient, le plus bas des 20 dernières séances et la dernière actu. Les actus de tes positions ne sont plus envoyées d'office : le bouton « En savoir plus » sous le résumé les rassemble à la demande, liens compris. | Toutes les 30 min |
| `opportunites` | Score sur 100 pour chaque valeur des thèmes suivis : catalyseur dans l'actu (partenariat, contrat, prise de participation, résultats relevés), dynamique, volume, accélération du jour. Claude affine la note du catalyseur. Au-dessus du seuil, alerte immédiate, notée au journal. | Toutes les 30 min |
| `evenements` | Lit l'actualité mondiale et des sources officielles (FDA, Agence européenne du médicament, contrats du Pentagone, énergie). Claude relie chaque événement aux valeurs suivies, surtout les effets de second rang, avec la chaîne de cause à effet. Au-dessus de 3/5 de confiance, alerte : avec un plan d'achat chiffré et ses boutons si une hausse est attendue, avec un rappel de ton stop si une baisse touche une de tes positions. Chaque lien est noté au journal et mesuré. | Toutes les 5 min (Claude au plus tous les quarts d'heure) |
| `officiels` | Sources officielles sur les sociétés suivies : communiqués réglementés des sociétés françaises (API info-financière de l'AMF), avis et autorisations de médicaments de l'EMA, autorisations de la FDA, essais de phase 3 terminés, arrêtés ou avec résultats (ClinicalTrials.gov). Gratuit et sans clé. Les sociétés sont reconnues par leur ISIN ou le nom du laboratoire grâce à l'annuaire FinanceDatabase. Chaque nouveauté part à ceux qui suivent la valeur et son effet est mesuré comme une news. Les déclarations répétitives (droits de vote, actions propres) sont écartées. | AMF toutes les 15 min, le reste toutes les 6 h |
| `portefeuille` | Valeur de chaque ligne en euros (devises converties au taux du jour), plus-value sur ton prix de revient, variation de la séance, poids dans son enveloppe et dans le total, puis les mêmes chiffres par enveloppe (PEA, CTO…). Signale une ligne qui dépasse 25 % du total. Une ligne sans cours (titre non coté) est comptée à son prix de revient et signalée. Suit la comparaison avec l'ETF de référence sur 1 semaine, 1 mois, 3 mois, 1 an et depuis le début, avec les deux courbes des 30 derniers jours : la veille photographie la valeur chaque jour, versements et ventes retirés du calcul pour qu'un apport ne passe pas pour un gain. Le bilan mensuel reprend cette comparaison. | À la demande, ou bouton « Détail du portefeuille » sous le résumé |
| `resume` | Valeur du portefeuille et de chaque enveloppe en tête, puis résumé du contexte, conseil sur chacune de tes positions (garder, alléger, prendre des gains, vendre), valeurs les mieux notées sur 100 dans chaque thème (avec la mention « PEA possible »), introductions en bourse à venir et dossiers déposés à la SEC, hypothèses notées au journal. | Chaque matin |
| `bilan` | Évalue les hypothèses arrivées à échéance : taux de réussite, gain moyen, et comparaison avec l'ETF de référence. | Chaque mois |
| `boucle` | Enchaîne tout automatiquement selon la section `planification` de la configuration. | En continu |

**Conseils chiffrés (phase 2).** Chaque opportunité retenue arrive avec un plan d'achat : ordre à cours limité, quantité, stop, objectif, et un montant calculé pour que la perte au stop reste vers 2 % de la poche dédiée (section `conseils`). La veille suit chaque conseil et t'écrit « vends » quand le stop ou l'objectif est atteint. Sur tes positions, un stop suiveur et la tendance donnent un avis : garder, alléger, prendre des gains ou vendre. Un stop ne protège pas d'une ouverture en forte baisse : la perte réelle peut alors dépasser celle prévue. Le bilan mensuel donne le résultat net des conseils, frais compris.

**Baisses inhabituelles.** Chaque valeur est jugée selon sa propre respiration : le repli en cours est comparé à tous ses replis des deux dernières années, la chute du jour à sa variation normale (en écarts-types). S'y ajoutent la cassure de la série de creux montants, le passage sous la moyenne 200 jours et l'emballement de la volatilité. Le tout donne un risque sur 100 ; au-dessus de 50, une alerte situe la baisse par rapport à tes gains déjà faits. Une baisse ordinaire pour une valeur nerveuse n'alerte plus. Ces signaux disent quand une baisse sort de l'ordinaire, ils ne prédisent pas l'avenir.

**Historique des news.** Chaque news porteuse d'un catalyseur est notée avec le cours du moment ; son effet est mesuré à 1 jour, 1 semaine et 4 semaines, en points d'écart à l'ETF de référence. Dès qu'une catégorie (contrat, partenariat, résultats…) compte 10 cas mesurés, son poids dans le score est corrigé par son effet réel, de la moitié à une fois et demie. Le bilan mensuel affiche l'effet moyen de chaque catégorie. Il faut plusieurs mois pour que ces chiffres veuillent dire quelque chose.

**Thèmes suivis.** L'IA (`theme_ia`) et les secteurs de la section `themes` passent tous par le même score sur 100. Chaque thème a son univers, ses mots-clés et son nombre de pistes dans le résumé. Les valeurs cotées en euros sont marquées « PEA possible », à confirmer chez ton courtier ; les autres vont sur le compte-titres. Pour ajouter les thèmes de l'exemple à un `config.yaml` existant qui n'a pas encore de section `themes` :

```bash
python3 -c "import yaml; e = yaml.safe_load(open('config.example.yaml')); open('config.yaml', 'a').write('\n' + yaml.safe_dump({'themes': e['themes']}, allow_unicode=True, sort_keys=False))"
```

**Actus non boursières.** Un site boursier qui conseille d'acheter arrive après le marché. La commande `evenements` part de faits bruts (un détroit fermé, un médicament autorisé, un contrat militaire, une canicule) et cherche qui en profite ou en souffre parmi tes valeurs, en privilégiant ce qui met des jours à se voir : la course à la seconde est perdue d'avance face aux robots des fonds. Chaque lien est noté comme catalyseur (catégories « événement direct » ou « indirect », hausse ou baisse attendue) et comme hypothèse ; le bilan mensuel compare les origines des hypothèses entre elles. Les flux, l'intervalle et le nombre d'appels à Claude se règlent dans la section `evenements`.

**Mémoire de l'IA.** Avec les news du jour, Claude reçoit une fiche courte recalculée depuis le journal : l'effet mesuré de chaque type de news par secteur, le bilan de ses hypothèses passées et ce qu'ont fait les cours après les dernières news de chaque valeur. Il raisonne ainsi sur des chiffres réels plutôt que de mémoire. Les secteurs se complètent dans la section `secteurs` de la configuration.

Les hypothèses sont rédigées par Claude (API Anthropic) si une clé est fournie. Sans clé, des règles simples prennent le relais.

## Installation

```bash
git clone https://github.com/oodarkriseoo/Boursicoteur.git
cd Boursicoteur
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp config.example.yaml config.yaml   # puis adapte ton portefeuille et les thèmes suivis
```

Secrets, dans des variables d'environnement (jamais dans `config.yaml`) :

```bash
export ANTHROPIC_API_KEY=...        # facultatif : résumés et hypothèses par Claude
export TELEGRAM_BOT_TOKEN=...       # bot créé avec @BotFather
export TELEGRAM_CHAT_ID=...         # ton identifiant de conversation
export NTFY_TOPIC=mon-sujet-secret  # ou ntfy à la place de Telegram
```

Essai :

```bash
python -m boursicoteur resume
```

## Faire tourner 24 h/24

Le plus simple est un petit serveur (VPS à environ 4 à 6 €/mois) avec un service systemd :

```ini
# /etc/systemd/system/boursicoteur.service
[Unit]
Description=Boursicoteur
After=network-online.target

[Service]
WorkingDirectory=/opt/Boursicoteur
EnvironmentFile=/opt/Boursicoteur/.env
ExecStart=/opt/Boursicoteur/.venv/bin/python -m boursicoteur boucle
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now boursicoteur
journalctl -u boursicoteur -f
```

## Partager le bot

D'autres personnes peuvent utiliser ton bot, chacune avec son portefeuille et ses valeurs à surveiller. Elles écrivent au même bot Telegram (donne-leur son nom, `@ton_bot`) :

1. Elles envoient `/start`. Tu reçois « X demande à utiliser le bot » avec **✅ Accepter** et **❌ Refuser**.
2. Une fois acceptées, elles saisissent leurs positions et leurs valeurs depuis Telegram :

| Commande | Effet |
|---|---|
| `/ajouter MC.PA 3 612,40 PEA LVMH` | Ajoute ou remplace une position : ticker Yahoo, quantité, prix de revient unitaire en euros, enveloppe (PEA, CTO… ; CTO par défaut), nom |
| `/retirer MC.PA` | Retire une position |
| `/stop MC.PA 580` | Seuil de sortie : alerte si le cours passe dessous (`0` l'enlève) |
| `/positions`, `/portefeuille` | Positions enregistrées ; valeur et plus-value |
| `/suivre ASML.AS SAF.PA` | Surveille des valeurs sans les détenir : alertes de chute et opportunités, et une rubrique « Tes valeurs surveillées » dans le résumé |
| `/chercher defense france` | Trouve des valeurs par nom, secteur ou pays (annuaire FinanceDatabase, plus de 120 000 actions) : les plus grandes d'abord, cotées en euros ou en dollars, avec un bouton pour les surveiller |
| `/oublier ASML.AS`, `/liste` | Ne plus surveiller ; voir la liste |
| `/themes` | Liste les domaines suivis (IA, santé, électrification, défense…). Un bouton par domaine montre ses 10 valeurs les mieux notées du jour, permet de suivre ou non ce domaine et de surveiller ses 3 meilleures valeurs d'un clic. `/themes sante` va directement au détail |
| `/poche 500` | Argent dédié aux conseils d'achat, qui fixe la taille des plans |
| `/quitter` | Efface toutes ses données (après confirmation) |

Ce qui est commun et ce qui est propre à chacun :
- **Commun** : liste des thèmes et de leurs valeurs, seuils, sources, le résumé de Claude (un seul appel par jour pour tout le monde) et la lecture des actus non boursières. Un utilisateur de plus ne coûte presque rien en API : les appels à Claude ne sont pas répétés, leurs données grossissent juste un peu avec les valeurs de chacun.
- **Propre à chacun** : positions, valeurs surveillées, alertes, conseils d'achat et leurs boutons, photo quotidienne et comparaison avec la référence, partie personnelle du bilan. Chacun choisit ses domaines avec `/themes` (tous par défaut) : il reçoit les opportunités et le classement du résumé de ces domaines, plus ce qui touche ses positions et ses valeurs surveillées. Les hypothèses du résumé suivent le même filtre.
- **Réservé à l'administrateur** : les ordres via IBKR (c'est ton compte) et les messages ntfy. `/utilisateurs` liste qui utilise le bot (noms et nombre de lignes, sans le détail).

À savoir :
- **Tes positions passent dans la base.** À la première mise en route de cette version, le portefeuille de `config.yaml` devient celui de l'administrateur (ta conversation, `TELEGRAM_CHAT_ID`), avec tes conseils et ton historique de valeur. Ensuite, la liste `portefeuille` de `config.yaml` n'est plus relue : modifie tes positions avec les commandes ci-dessus.
- **Confidentialité.** Les portefeuilles des autres sont stockés en clair dans `data/journal.sqlite` sur ton serveur : techniquement, tu peux les lire. Le message d'accueil le leur dit. Le bot ne répond qu'en conversation privée, jamais dans un groupe.
- **Accès.** Personne n'entre sans ton accord ; `utilisateurs.max` (10 par défaut) borne le nombre de personnes, et au-delà de 5 demandes en attente les suivantes sont refusées d'office.
- **Pas de conseil en investissement réglementé.** Le bot donne des alertes et des plans chiffrés, il ne connaît ni la situation ni les objectifs des autres. Pour des proches, c'est un outil partagé ; au-delà (abonnement payant, public), la réglementation sur le conseil en investissement s'appliquerait.

## Boutons Telegram et ordres via IBKR

Chaque conseil d'achat arrive avec des boutons :
- **✅ Déjà acheté** : tu as passé l'ordre toi-même (Trade Republic par exemple) ; les alertes de vente suivent cet achat.
- **❌ Ignorer** : le conseil est suivi en silence, pour que le bilan compte aussi ce que tu écartes.
- **🛒 Acheter via IBKR** (si le courtier est branché) : le bot affiche un récapitulatif, puis **✔️ Confirmer** envoie à Interactive Brokers un achat à cours limité avec le stop et l'objectif attachés. IBKR les exécute même si ton serveur est éteint.

Seuls les utilisateurs acceptés peuvent déclencher un bouton, chacun sur ses propres conseils ; les ordres IBKR sont réservés à l'administrateur. Un ordre est refusé s'il dépasse `plafond_ordre_eur`, si la journée dépasse `plafond_jour_eur`, si le conseil a plus de 24 h, ou si un compte réel est connecté alors que `compte_demo` est vrai.

### Ordres via IBKR

1. Ouvre un compte Interactive Brokers (compte-titres). Dans le portail client, active le **compte de démonstration** (Paper Trading) : il commence par « DU ».
2. Sur le serveur, installe Java et **IB Gateway** (version « stable », Linux) depuis le site d'IBKR, puis **IBC** (github.com/IbcAlpha/IBC) pour qu'il redémarre seul. Connecte-le au compte démo ; il écoute alors sur le port 4002. Dans les réglages de l'API, coche « Enable ActiveX and Socket Clients » et ajoute 127.0.0.1 aux adresses de confiance.
3. Installe le connecteur : `.venv/bin/pip install -e ".[ibkr]"`.
4. Dans `config.yaml`, section `courtier`, mets `type: ibkr` et redémarre le service.
5. Teste un mois en démo. Pour passer en réel : port 4001, `compte_demo: false`, plafonds prudents.

IBKR impose une reconnexion avec la double authentification (appli IBKR Mobile) environ une fois par semaine : IBC te la demande, il suffit de valider sur ton téléphone.

## Budget de départ

| Poste | Coût |
|---|---|
| Serveur | 0 à 6 €/mois |
| Cours (Yahoo Finance via yfinance), actus RSS, calendrier Nasdaq, SEC | 0 € |
| Claude, un résumé par jour et la notation des catalyseurs frais | quelques € à une quinzaine d'€/mois |
| Telegram ou ntfy | 0 € |

## Sources et limites

- **Cours** : yfinance, gratuit mais non officiel. Il peut cesser de fonctionner quelque temps si Yahoo change son site ; l'outil continue alors sans ces données.
- **Introductions en bourse** : calendrier Nasdaq (API publique non documentée) et dépôts de formulaires S-1 / F-1 à la SEC.
- **Actus** : flux RSS (BCE, Fed, presse économique, Yahoo Finance par valeur).
- Les petites valeurs et introductions américaines ne sont presque jamais éligibles au PEA : elles se logent sur le compte-titres.

## Feuille de route

1. **Phase 1 (ce dépôt)** : observation et alertes. Aucun ordre, aucun argent engagé sur les signaux.
2. **Phase 2 (en place)** : conseils d'achat et de vente chiffrés, passés à la main avec une petite poche plafonnée. N'augmente la poche que si le bilan des conseils reste positif, frais compris, sur plusieurs mois.
3. **Phase 3, facultative** : passage d'ordres via l'API d'un courtier qui en propose une (Interactive Brokers, Saxo), avec garde-fous stricts et confirmation de ta part.

## Développement

```bash
pytest
```

*Ceci n'est pas un conseil en investissement. Investir comporte un risque de perte en capital.*
