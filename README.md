# Boursicoteur

Outil de veille boursière qui tourne 24 h/24. Il suit tes positions (PEA, compte-titres), les actus et le contexte économique, repère les valeurs liées à l'intelligence artificielle qui s'animent, surveille les introductions en bourse, formule des hypothèses haussières ou baissières et te prévient sur Telegram ou ntfy.

**Il ne passe aucun ordre.** Il t'informe, tu décides et tu cliques dans l'appli de ton courtier (Trade Republic par exemple, qui n'a pas d'API officielle). Chaque hypothèse est notée dans un journal puis évaluée à son échéance, pour savoir honnêtement si l'outil fait mieux que de garder un ETF MSCI World.

## Ce qu'il fait

| Commande | Rôle | Fréquence conseillée |
|---|---|---|
| `veille` | Alertes de chute : séance (puis à chaque palier de 3 points), chute rapide entre deux contrôles, passage sous ton seuil de sortie (`stop`), recul depuis le plus haut, perte par rapport à ton prix de revient, VIX élevé. Chaque alerte de chute rappelle ton écart au prix de revient, le plus bas des 20 dernières séances et la dernière actu. Actus nouvelles sur tes valeurs. | Toutes les 30 min |
| `opportunites` | Score sur 100 pour chaque valeur de l'univers IA : catalyseur dans l'actu (partenariat, contrat, prise de participation, résultats relevés), dynamique, volume, accélération du jour. Claude affine la note du catalyseur. Au-dessus du seuil, alerte immédiate, notée au journal. | Toutes les 30 min |
| `resume` | Résumé du contexte, valeurs IA les mieux notées sur 100, introductions en bourse à venir et dossiers déposés à la SEC, hypothèses notées au journal. | Chaque matin |
| `bilan` | Évalue les hypothèses arrivées à échéance : taux de réussite, gain moyen, et comparaison avec l'ETF de référence. | Chaque mois |
| `boucle` | Enchaîne tout automatiquement selon la section `planification` de la configuration. | En continu |

Les hypothèses sont rédigées par Claude (API Anthropic) si une clé est fournie. Sans clé, des règles simples prennent le relais.

## Installation

```bash
git clone https://github.com/oodarkriseoo/Boursicoteur.git
cd Boursicoteur
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp config.example.yaml config.yaml   # puis adapte ton portefeuille et l'univers IA
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
2. **Phase 2** : si le bilan montre un avantage net après frais sur plusieurs mois, suivre certains signaux à la main avec une petite poche plafonnée (par exemple 10 % du portefeuille).
3. **Phase 3, facultative** : passage d'ordres via l'API d'un courtier qui en propose une (Interactive Brokers, Saxo), avec garde-fous stricts et confirmation de ta part.

## Développement

```bash
pytest
```

*Ceci n'est pas un conseil en investissement. Investir comporte un risque de perte en capital.*
