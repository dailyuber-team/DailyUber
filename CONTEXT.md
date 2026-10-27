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
- **Horaires vs créneaux** (observé le 22/10 à 12h47, 40 restaurants) :
  - `hours` couvre toujours 7 jours. Plages de jours vues : « Tous les jours », « Lundi - Samedi », « Dimanche - Jeudi » (passe par le week-end), jours seuls. Aucune plage passant minuit dans l'échantillon, mais le code le gère.
  - `adaptedDeliveryHoursInfos` donne des créneaux de 30 min tous les 15 min, sur un horizon d'environ 7 jours. Les jours fermés sont absents.
  - **Les créneaux sont plus précis que les horaires**, et c'est sur eux qu'on se fonde :
    - le dernier créneau peut dépasser la fermeture (22h15–22h45 pour une fermeture à 22h30) ;
    - le premier commence après l'ouverture (temps de préparation) ;
    - un commerce peut arrêter ses livraisons avant de fermer (Carrefour : ouvert jusqu'à 20h30, livraisons jusqu'à 20h).
- ⚠️ **Pas encore vérifié** : le comportement **le soir et la nuit**. Le flux liste-t-il les restaurants fermés ? Le pré-filtre gaspille-t-il des requêtes sur des restaurants fermés ? `isOpen` et les créneaux du jour sont-ils corrects ? Le navigateur intégré a refusé d'ouvrir ubereats.com le 22/10 : la comparaison visuelle avec le site n'a donc pas été faite.

## 4. État actuel du code (27/10/2026 : moteur, horaires, appli mobile et favoris faits ; phase 2 à revérifier le soir)

Dépôt GitHub **public** (rendu public par emmagmzc le 27/10 pour GitHub Pages) : https://github.com/dailyuber-team/DailyUber (branche `main`), dans l'organisation `dailyuber-team` (propriétaire : Anatole-1 ; collaboratrice emmagmzc, admin du dépôt). L'ancienne adresse `Anatole-1/DailyUber` n'est qu'une redirection : **ne jamais la « supprimer »**, ça viserait le vrai dépôt. Le CLI GitHub `gh` est installé (`C:\Program Files\GitHub CLI\gh.exe`, absent du PATH du shell bash) et connecté au compte Anatole-1.

```
DailyUber/
  config.yaml            # adresse, délais, cache, pré-filtre, frais estimés, définition d'un repas, sélection, scoring
  .env.example           # DISCORD_WEBHOOK_URL (pas encore utilisé)
  requirements.txt       # playwright==1.64.0, PyYAML, python-dotenv, pytest, tzdata (fuseau Paris sous Windows)
  dailyuber/
    config.py            # charge config.yaml et .env
    models.py            # Item, ThresholdPromo, Store (dont is_open, open_until, hours, delivery_slots), Basket (dont availability)
    fetcher.py           # UberEatsSession (Playwright) : fetch_feed(), fetch_store(), délais aléatoires
    parser.py            # flux et store → modèles ; promos à seuil, frais de livraison, horaires et créneaux
    hours.py             # parse_day_range, parse_hours, is_open_at, parse_delivery_slots, availability, resolve_target
    selection.py         # MealClassifier, item_cost (1+1), compute_fees, best_basket (exact), brand_key, select_deals(when=)
    engine.py            # get_raw (cache 30 min), fetch_raw, load_cached_raw, build_stores, select : API du moteur
    history.py           # data/history.json : restaurants nouveaux dans le top par rapport au run précédent
    report.py            # rendu console
    main.py              # CLI
    export.py            # data.json de l'appli : restaurants avec menu, catalogue léger, favoris
  web/                   # appli mobile (PWA) : index.html, app.js, engine.js, style.css, sw.js, manifest, icônes
  .github/workflows/site.yml  # recherche quotidienne vers 10 h + publication GitHub Pages
  package.json           # "type": "module" pour Node (voir plus bas)
  tests/test_selection.py  # frais, 1+1, promos, classification, algorithme vs recherche exhaustive, doublons
  tests/test_hours.py      # plages de jours, passage de minuit, disponibilité maintenant / créneau / horaires
  tests/test_favorites.py  # favoris toujours récupérés, favori hors flux, cache, export
  tests/test_parity.py     # mêmes offres en Python et en JavaScript (Node.js requis)
  data/raw/latest.json.gz  # dernière récupération brute (cache, permet --offline)
```

**Appli mobile** (par emmagmzc, 27/10) : PWA statique dans `web/` (index.html, style.css, app.js, engine.js, sw.js, manifest, icônes), publiée sur GitHub Pages par `.github/workflows/site.yml`.
- Chaque jour vers 10 h (Paris), GitHub Actions lance la recherche sur **tous** les restaurants qui passent le pré-filtre (`python -m dailyuber.export --refresh --max-stores 0`, environ 210 menus, 16 min ; 40 seulement en local, plus les favoris), garde la récupération brute dans le cache d'Actions, et publie `data.json` (plats principaux seulement, types de repas précalculés, frais, promos, horaires).
- Le téléphone recalcule les offres en JavaScript (`web/engine.js`, portage fidèle de `selection.py` et `hours.availability`, y compris l'arrondi de Python) : personnes 1 à 6, heure (maintenant / aujourd'hui / demain), budget, Uber One, types de repas. **Toute modification de selection.py ou hours.py doit être reportée dans engine.js** ; `tests/test_parity.py` (Node.js requis) compare les deux sur des données aléatoires et sur la dernière récupération réelle.
- Types de repas : `meal_types` dans config.yaml (mots-clés du plat, sinon nom du restaurant et `cuisineList`), dont « Crousty » (14 restos spécialisés à Aix, tous hors des 40 premiers du pré-filtre : d'où la recherche complète) ; CLI `--type` et `--budget`. `meal.always_exclude_keywords` (nutella, chocolat…) exclut un dessert même si son titre contient un mot-clé de plat.
- Logo (27/10) : `design/logo.webp` (boîtes repas crème + pièce verte « € ↓ »), icônes générées par `tools/make_icons.py` (Playwright). Couleurs de l'appli tirées du logo dans `web/style.css` : fond crème #f7f2ea, vert de la pièce #0b6b50 (dégradé #2a9a72 → #0a5e47 pour les boutons), menthe #63d893 en sombre, étoiles dorées.
- Classement de l'appli : par note (arrondie à 0,1 comme affichée, sans note à la fin), puis prix par personne (`rankDeals(..., "rating")`, `select_deals(sort="rating")`, CLI `--sort rating`). Le CLI garde le score par défaut.
- `web/engine.js` : `pyRound` a un chemin rapide exact hors cas d'égalité, et les tables de coûts sont communes aux seuils d'un panier (191 restaurants : 3 à 7 ms par calcul sur PC).
- Choix de l'utilisatrice : dépôt public + GitHub Pages, noir et blanc, thème clair/sombre selon le téléphone. Risque à surveiller : Uber peut bloquer les adresses IP de GitHub (pas le cas au 27/10 : 260 restaurants et 40 menus récupérés depuis GitHub Actions).
- `data.json` (`FORMAT_VERSION = 2`) : `stores` (avec menu), `catalog` (tous les autres restaurants du flux, sans menu : nom, lien, note, délai) et `favorites` (UUID des favoris de config.yaml).
- ⚠️ **Node et `package.json`** : un `C:\Users\anato\package.json` (sans rapport avec le projet) déclare `"type": "commonjs"`, ce qui faisait échouer `tests/test_parity.py` en local. Le `package.json` du projet déclare `"type": "module"` pour s'en affranchir : ne pas le supprimer.

**Favoris** (27/10) :
- `favorites` dans config.yaml : liens Uber Eats (`…/store/<nom>/<id base64>`) ou UUID (`parser.store_uuid_from_ref`). Ces restaurants sont **toujours récupérés**, en plus des `max_stores` du pré-filtre, même absents du flux du jour (`getStoreV1` par UUID ; le Store est alors reconstruit par `parser.parse_store_only`). Le cache est périmé si la liste des favoris change.
- Appli : étoile sur chaque offre, onglet **Offres | Favoris (n)**, recherche par nom dans `stores` + `catalog`. Les étoiles sont mémorisées sur le téléphone (`localStorage` `dailyuber:favorites`) ; ceux de config.yaml sont marqués « Suivi chaque jour » et ne se retirent pas depuis l'appli.
- Onglet Favoris : meilleur panier de chaque favori avec les réglages du moment (personnes, heure, Uber One), **tous types de plats et même au-dessus du budget** (prix grisé « au-dessus de X € »). Sinon une carte explique pourquoi : fermé / pas de livraison à cette heure, menu pas analysé aujourd'hui (favori seulement sur le téléphone → l'ajouter à config.yaml), absent de la recherche du jour.
- Pas de changement dans `engine.js` : l'onglet réutilise `bestBaskets` et `availability`, la parité Python/JS n'est pas touchée.

**Lancer** (68 tests : `.venv\Scripts\python -m pytest -q`) :
- `.venv\Scripts\python -m dailyuber.main` : utilise le cache s'il a moins de 30 min, sinon récupère (environ 3 min pour 40 menus).
- `--refresh` : force une nouvelle récupération ; `--offline` : utilise le cache quel que soit son âge.
- `--people 1 3 6` : nombre(s) de personnes.
- `--at 20:00` : heure de livraison visée, aujourd'hui (ou demain si elle est passée) ; `--on demain` ou `--on 2026-10-24` pour choisir le jour.

**Logique actuelle :**
1. Flux (3 pages, environ 257 restaurants).
2. Pré-filtre : note ≥ 4.0, délai ≤ 45 min, restaurants avec promo en premier, 40 maximum.
3. `getStoreV1` pour chacun.
4. **Disponibilité** (`hours.availability`) :
   - « maintenant » (ou heure visée dans moins de 30 min) : il faut `isOpen` et `isOrderable` à la récupération, ET que le restaurant soit encore ouvert d'après ses horaires à l'heure actuelle. Libellé : « Ouvert jusqu'à 22:00 ».
   - heure future : si la date est dans l'horizon des créneaux, il faut un créneau qui contient l'heure visée (on garde celui qui commence le plus tard) ; libellé « Créneau 12:30–13:00 ». Au-delà de l'horizon, on se fie aux horaires : « Ouvert à cette heure (non planifiable…) ».
5. Repas = article ≥ 6 € dont le titre ou la section contient un mot-clé « plat », sans mot-clé d'exclusion. La section peut exclure un article (« Accompagnements »), mais « Menu burger frites » reste un plat.
6. **Panier optimal exact** pour N plats (1 à 6) :
   - Le coût total ne dépend que du sous-total et croît avec lui, sauf aux seuils (promo « dès Y € », livraison offerte dès X €, petite commande < 8 €).
   - Pour chaque seuil, `_min_subtotal_lines` cherche le plus petit sous-total qui l'atteint, par recherche avec élagage (en centimes). On garde ensuite le meilleur total.
   - Candidats : les 40 plats les moins chers + tous les 1+1. Option `min_distinct_items`.
   - Environ 15 ms pour 40 restaurants, même à 6 personnes.
7. Total = articles (après 1+1) − meilleure promo à seuil + livraison (défaut 2,49 €, valeur affichée, ou offerte sous condition) + service estimé (10 %, entre 0,99 € et 4,99 €) + petite commande (2 € si < 8 €).
8. On garde les paniers à moins de 10 €/pers, classés par score : 0,6 × prix + 0,25 × note + 0,15 × délai. Une seule offre par enseigne (`brand_key`) et par composition identique. Top 5 par taille de groupe.
9. L'historique « nouveau » n'est mis à jour que pour les recherches « maintenant ».

**Limites connues :**
- Frais de service estimés, pas réels.
- **Pour une heure future, le menu et les prix sont ceux du moment de la récupération** : une « formule midi » récupérée à midi sera proposée pour un dîner. Ce n'est pas encore géré.
- Le pré-filtre ne sait pas exclure les restaurants fermés (le flux n'est pas fiable sur ce point). Le soir, des requêtes peuvent être gaspillées sur des restaurants fermés. À vérifier.
- Les promos « article offert » sont signalées mais pas chiffrées.
- Les options obligatoires des articles personnalisables sont ignorées.
- La classification des repas par mots-clés est imparfaite (« Maki anguille grillée 6 € » passe pour un plat).

## 5. Décisions déjà prises

- Python + Playwright (session navigateur) + endpoints JSON. Pas de parsing HTML.
- Pas de connexion à un compte Uber, jamais.
- Requêtes séquentielles avec un délai aléatoire de 2 à 5 s, user-agent réaliste, pas de parallélisme.
- Secrets dans `.env` / GitHub Secrets.
- Discord (webhook + embeds + alerte en cas d'erreur) et la planification quotidienne à 11h00, heure de Paris, étaient prévus. Ils sont **reportés** après l'interface graphique (voir la roadmap).

## 6. Nouvelles demandes (voir ROADMAP.md pour l'avancement)

1. Interface graphique simple et épurée. ✅ (appli mobile PWA d'emmagmzc, utilisable aussi sur PC dans le navigateur)
2. Nombre de personnes réglable de 1 à 6. ✅
3. Enseignes favorites : toujours analysées et affichées dans un onglet. ✅
4. Application portable : PC d'abord, téléphone ensuite. ✅ côté téléphone (PWA sur GitHub Pages) ; pas d'exécutable PC autonome.
5. Choix d'une zone géographique dans laquelle les offres sont cherchées.
6. Vérification fiable des horaires de commande et de l'ouverture des restaurants. ✅ (à revérifier le soir)
