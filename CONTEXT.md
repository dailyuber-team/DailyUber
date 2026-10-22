# DailyUber — Fichier de contexte

> À coller (ou à joindre) au début d'une nouvelle conversation avec Claude.
> Dernière mise à jour : 22/10/2026. Voir aussi [ROADMAP.md](ROADMAP.md).

## 1. Le projet en une phrase

Un outil perso qui parcourt les offres Uber Eats livrables autour d'Aix-en-Provence (13100) et propose les meilleurs "deals" pour commander à N personnes, avec un coût total (plats + frais − promos) **inférieur à 10 € par personne**.

## 2. Façon de travailler (préférences de l'utilisateur)

- Échanges **en français**.
- Avancer **par étapes** et **attendre la validation** avant une étape structurante (choix d'architecture, branchement d'un service externe).
- Toujours **montrer un run réel** (sortie console ou capture) avant d'aller plus loin.
- Préférer la solution **la plus simple et la plus fiable** ; Claude peut contester les choix proposés s'il a mieux.
- Utilisateur sous **Windows 11**, Python 3.14 (lancé via `py`, la commande `python` n'existe pas), dossier `C:\Users\anato\Documents\DailyUber`. Pas encore de dépôt git.

## 3. Ce qui a été établi lors de l'investigation (important, coûteux à redécouvrir)

### Aspects légaux et risques
- **robots.txt** (`User-agent: *`) n'interdit que `*/search?`, `*/delivery-details`, `*/group-orders/`, `*/select-language`. Les endpoints utilisés ne sont pas interdits.
- **CGU Uber France** : interdisent explicitement de « lancer ou faire lancer tout programme ou script dans le but de récupérer, d'indexer, d'étudier ou d'extraire des données ». L'outil **viole donc les CGU** ; l'utilisateur l'a accepté en connaissance de cause.
- Risques : pas de risque de compte (on **ne se connecte jamais**), blocage d'IP possible mais peu probable à faible volume, fragilité si Uber change ses endpoints internes. Les IP de datacenter (GitHub Actions, VPS) sont plus exposées au blocage que l'IP d'un particulier.

### Récupération des données
- Pas d'API publique. On utilise les **endpoints JSON internes** du site : `POST https://www.ubereats.com/_p/api/<Endpoint>?localeCode=fr`, en-têtes `content-type: application/json` et `x-csrf-token: x`.
- `getStoreV1` (`{"storeUuid": ...}`) répond même à un simple script Python.
- `getFeedV1` renvoie **403 hors navigateur** → on ouvre une vraie page avec **Playwright** (Chromium headless, ça passe), puis on appelle les endpoints **depuis la page** (`page.evaluate(fetch…)`).
- **Adresse** : on ouvre `https://www.ubereats.com/fr/feed?diningMode=DELIVERY&pl=<pl>` avec `pl = base64(urlencode(JSON))`, où le JSON vaut `{"address","latitude","longitude","reference","referenceType":"google_places"}`. `reference` (ID Google Places) est facultatif.
- `getLocationAutocompleteV1` **n'existe plus** (404). L'autocomplétion d'adresse du site passe par `mapsSearchV1` (pas encore exploré).
- **Pagination du flux** : la 1re requête se fait sans `pageInfo`, les suivantes avec `"pageInfo": {"offset": meta.offset, "pageSize": 80}`. Environ 256 restaurants sur 3 pages au Cours Mirabeau.
- Un `uuid` de restaurant s'obtient aussi à partir de l'URL : `/store/<slug>/<id base64url>`, et l'id décodé donne l'UUID.

### Où trouver chaque donnée
| Donnée | Source |
|---|---|
| Nom, note, nombre d'avis | flux : `store.title.text`, `tracking.storePayload.ratingInfo` (`ratingCount` peut valoir `"3,000+"`) |
| Délai de livraison | flux : `tracking.storePayload.etdInfo.dropoffETARange.{min,max}` |
| Coordonnées GPS du restaurant | flux : `store.mapMarker.{latitude,longitude}` ; store : `location.{latitude,longitude,address}` |
| Badges promo | flux : `store.signposts[].text`, par ex. « 1 acheté = 1 offert », « -20% dès 20 € d'achat », « Article offert dès 16 € d'achat », « 30% offert sur plusieurs articles » |
| Frais de livraison | flux : `store.meta[]` avec `badgeType == "FARE"` (« Frais de livraison : 1.49 € ») — **rarement présent**. Le texte « 0.00 € (nouveaux utilisateurs) » est affiché aux visiteurs anonymes et **doit être ignoré**. Il existe aussi « Frais de livraison à 0 € sur les commandes supérieures à 15 € » (livraison offerte sous condition). |
| Frais de service | **Non exposés hors checkout** (compte requis) → estimés : % du panier avec min/max, dans la config |
| Petite commande | texte officiel d'Uber : +2 € si commande < 8 € (restaurants) |
| Menu | store : `catalogSectionsMap[*][*].payload.standardItemsPayload.{title.text, catalogItems[]}` ; un même article apparaît dans plusieurs sections (dédoublonner par `uuid`) |
| Prix d'un article | `price` en centimes, **remises % déjà appliquées** ; prix d'origine dans `priceTagline.accessibilityText` (« discounted from 13,50 € ») |
| 1 acheté = 1 offert | `itemPromotion.buyXGetYItemPromotion.{buyQuantity,getQuantity,maxRedemptionCount}` |
| Disponibilité de l'article | `isSoldOut`, `isAvailable`, `hasCustomizations` (le prix de base peut augmenter selon les options) |
| Ouverture | store : `isOpen`, `isOrderable`, `closedMessage`, `workingHoursTagline` (« Ouvert jusqu'à 19:30 ») |
| Horaires | store : `hours[] = {dayRange: "Lundi - Vendredi", sectionHours: [{startTime: 630, endTime: 1170}]}` (minutes depuis minuit) |
| Créneaux planifiables | store : `scheduledOrderInfo.isSchedulable`, `adaptedDeliveryHoursInfos.{dates[], timeRanges{date: [{startTime,endTime}]}}` |

- ⚠️ Dans le flux, `storeAvailablityState` valait `NOT_ACCEPTING_ORDERS` pour **tous** les restaurants, y compris les ouverts : ce champ **n'est pas fiable**. Il faut utiliser `isOpen`/`isOrderable` de `getStoreV1`.
- Les autres champs liés aux horaires (`hours`, `adaptedDeliveryHoursInfos`) n'ont été observés qu'une fois, en journée. Il faut encore vérifier leur comportement **le soir et quand le restaurant est fermé**.

## 4. État actuel du code (roadmap : phases 0 et 1 faites, en attente de validation)

Dépôt GitHub privé : https://github.com/dailyuber-team/DailyUber (branche `main`), dans l'organisation `dailyuber-team` (propriétaire : Anatole-1 ; collaboratrice emmagmzc, admin du dépôt). Le CLI GitHub `gh` est installé (`C:\Program Files\GitHub CLI\gh.exe`, absent du PATH du shell bash) et connecté au compte Anatole-1.

**Phases 0 et 1 :**
- `dailyuber/engine.py` (`get_raw` avec cache de 30 min, `build_stores`, `select`) est le point d'entrée réutilisable par la future API.
- CLI : `--refresh`, `--offline`, `--people 1..6`.
- Nouvel algorithme exact (`_min_subtotal_lines` + seuils dans `best_basket`).
- Option `selection.min_distinct_items`.
- Fusion des doublons par enseigne normalisée (`brand_key`) ou par composition identique.
- 37 tests.

```
DailyUber/
  config.yaml            # adresse, délais, pré-filtre, frais estimés, définition d'un repas, scoring
  .env.example           # DISCORD_WEBHOOK_URL (pas encore utilisé)
  requirements.txt       # playwright==1.64.0, PyYAML, python-dotenv, pytest
  dailyuber/
    config.py            # charge config.yaml et .env
    models.py            # Item, ThresholdPromo, Store, Basket (total, prix par personne, clé)
    fetcher.py           # UberEatsSession (Playwright) : fetch_feed(), fetch_store(), délais aléatoires
    parser.py            # flux et store → modèles ; parse les promos à seuil et les frais de livraison
    selection.py         # MealClassifier, item_cost (1+1), compute_fees, best_basket, score, select_deals
    history.py           # data/history.json : marque les restaurants nouveaux dans le top par rapport au run précédent
    report.py            # rendu console
    main.py              # python -m dailyuber.main [--offline]
  tests/test_selection.py  # 26 tests (frais, 1+1, promos à seuil, classification, composition, filtres)
  data/raw/latest.json.gz  # dernière récupération brute (permet --offline)
```

**Lancer :**
- `.venv\Scripts\python -m dailyuber.main` : récupération réelle, environ 3 min pour 40 menus.
- `... --offline` : relance la sélection sur la dernière récupération, sans requête.
- Tests : `.venv\Scripts\python -m pytest -q`.

**Logique actuelle :**
1. Flux (3 pages).
2. Pré-filtre : note ≥ 4.0, délai ≤ 45 min, restaurants avec promo en premier, 40 maximum.
3. `getStoreV1` pour chacun.
4. Repas = article ≥ 6 € dont le titre ou la section contient un mot-clé « plat », sans mot-clé d'exclusion. La section peut exclure un article (« Accompagnements »), mais « Menu burger frites » reste un plat.
5. Panier = combinaison la moins chère de N plats parmi les 20 moins chers + tous les articles en 1+1 (`combinations_with_replacement`, répétitions autorisées).
6. Total = articles (après 1+1) − meilleure promo à seuil + livraison (défaut 2,49 €, ou valeur affichée, ou offerte sous condition) + service estimé (10 %, entre 0,99 € et 4,99 €) + petite commande (2 € si < 8 €).
7. On garde les paniers à moins de 10 €/pers, classés par score : 0,6 × prix + 0,25 × note + 0,15 × délai.
8. Top 5 pour 2 et pour 4 personnes.

**Limites connues :**
- Frais de service estimés, pas réels.
- L'algorithme de composition **explose au-delà de 4 personnes** : C(25,6) ≈ 177 000 combinaisons par restaurant. Il faut le remplacer (voir la roadmap).
- Les promos « article offert » sont signalées mais pas chiffrées.
- Les options obligatoires des articles personnalisables sont ignorées.
- La classification des repas par mots-clés est imparfaite (« Maki anguille grillée 6 € » passe pour un plat).
- Deux enseignes identiques peuvent apparaître toutes les deux (deux « Chez Matias »).

## 5. Décisions déjà prises

- Python + Playwright (session navigateur) + endpoints JSON. Pas de parsing HTML.
- Pas de connexion à un compte Uber, jamais.
- Requêtes séquentielles avec un délai aléatoire de 2 à 5 s, user-agent réaliste, pas de parallélisme.
- Secrets dans `.env` / GitHub Secrets.
- Discord (webhook + embeds + alerte en cas d'erreur) et la planification quotidienne à 11h00, heure de Paris, étaient prévus. Ils sont **reportés** après l'interface graphique (voir la roadmap).

## 6. Nouvelles demandes (à traiter, voir ROADMAP.md)

1. Interface graphique simple et épurée.
2. Nombre de personnes réglable de 1 à 6.
3. Enseignes favorites : toujours analysées et affichées en priorité.
4. Application portable : PC d'abord, téléphone ensuite.
5. Choix d'une zone géographique dans laquelle les offres sont cherchées.
6. Vérification fiable des horaires de commande et de l'ouverture des restaurants.
