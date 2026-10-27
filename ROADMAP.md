# DailyUber — Roadmap

> Contexte technique complet : [CONTEXT.md](CONTEXT.md). Chaque phase se termine par un **run réel montré à l'utilisateur** et sa **validation** avant de passer à la suivante.

## Architecture cible (proposée)

```
┌─────────────── Moteur Python (existant, à étendre) ───────────────┐
│ fetcher (Playwright) → parser → horaires/zone/favoris → sélection │
└───────────────────────────────┬───────────────────────────────────┘
                                │ API HTTP locale (FastAPI)
                ┌───────────────┴───────────────┐
         Interface web (HTML + JS + carte Leaflet), responsive
         PC : s'ouvre dans le navigateur      Téléphone : même interface (PWA)
```

**Pourquoi une interface web locale plutôt qu'une appli de bureau (Tkinter, Qt…)**
- La **même interface** servira sur PC puis sur téléphone : pas de deuxième application à écrire.
- Carte et choix de zone faciles avec Leaflet + OpenStreetMap (gratuit, sans clé).
- Simple à rendre épurée (HTML/CSS), sans framework lourd.

**Contrainte structurante pour le téléphone** : la récupération des données a besoin d'un vrai navigateur piloté par Playwright, ce qui **ne peut pas tourner sur un téléphone**. Le moteur tourne donc toujours sur un PC (ou un petit serveur), et le téléphone ne fait qu'afficher (voir phase 6).

**Données récupérées une fois, sélection instantanée** : la récupération (environ 3 min) ne dépend ni du nombre de personnes ni des réglages. On la met en cache (par ex. 30 min) ; changer le nombre de personnes ou les filtres dans l'interface recalcule en moins d'une seconde, sans nouvelle requête vers Uber Eats.

---

## Phase 0 — Socle ✅ (22/10/2026)
- `git init` + premier commit.
- Transformer `main.py` en bibliothèque : `fetch(zone, favoris) → données brutes` et `select(données, paramètres) → résultats`, réutilisables par l'API.
- Cache des données brutes avec durée de validité.
- Fusionner les enseignes en double (même nom normalisé → garder la meilleure offre).

**Validé quand** : les commandes `--offline` et réelle donnent le même résultat qu'avant, et les tests passent.

## Phase 1 — 1 à 6 personnes ✅ (22/10/2026)
- `combinations_with_replacement` (≈ 177 000 combinaisons par restaurant pour 6 personnes) est remplacé par une **recherche avec élagage par seuil**. Le total ne dépend que du sous-total et croît avec lui, sauf aux seuils (promo, livraison offerte, petite commande). On cherche donc, pour chaque seuil, le plus petit sous-total qui l'atteint. C'est exact, vérifié contre une recherche exhaustive, et prend environ 15 ms pour 40 restaurants, même à 6 personnes.
- Option **« varier les plats »** (au moins K plats différents), désactivée par défaut.
- `group_sizes` devient un paramètre de requête (1 à 6) au lieu d'une liste figée.

**Validé quand** : les tests couvrent 1, 3 et 6 personnes, et la sélection pour 6 personnes sur 40 restaurants prend moins d'1 s.

## Phase 2 — Horaires et ouverture fiables 🟡 (code fait le 22/10/2026, vérification du soir à faire)
- ✅ Parsing de `hours` (plages de jours en texte, passage de minuit), `isOpen`/`isOrderable`, `workingHoursTagline` et `adaptedDeliveryHoursInfos` (créneaux par date) : `dailyuber/hours.py`.
- ✅ Deux modes : **« maintenant »** (accepte les commandes ET encore ouvert d'après les horaires) ou **`--at HH:MM [--on demain|AAAA-MM-JJ]`**. Le mode futur exige un créneau planifiable contenant l'heure ; au-delà de l'horizon des créneaux (environ 7 jours), on se fie aux horaires.
- ✅ Affiché dans les résultats : « Ouvert jusqu'à 22:00 » ou « Créneau 12:30–13:00 ».
- ✅ Run du 22/10 à 12h47, sur 40 restaurants :
  - restaurants disponibles : 40 maintenant, 30 à 20h, 13 à 22h30, 38 vendredi midi, 29 samedi midi, 18 dimanche midi ;
  - écarts entre créneaux et horaires tous expliqués (temps de préparation, dernier créneau qui dépasse la fermeture, fin des livraisons avant la fermeture). Les créneaux font foi.
- ⏳ **Reste à vérifier en réel le soir (vers 22h)** avec `--refresh` : le flux liste-t-il les restaurants fermés ? `isOpen` est-il faux pour eux ? Le pré-filtre gaspille-t-il des requêtes ? Comparer visuellement 2 ou 3 restaurants avec le site (pas fait le 22/10, le navigateur intégré a refusé ubereats.com).
- ⏳ À traiter plus tard : pour une heure future, le menu vient du moment de la récupération (une formule midi peut être proposée pour le soir).

**Validé quand** : un run à 22h et un run planifié pour le lendemain midi donnent des listes cohérentes avec le site.

## Phase 3 — Interface graphique ✅ autrement (27/10/2026, emmagmzc)
> Fait sous forme d'**appli web statique (PWA)** plutôt qu'avec FastAPI : la recherche tourne sur GitHub Actions, `data.json` est publié sur GitHub Pages et le calcul se fait dans le navigateur (`web/engine.js`). Le plan d'origine ci-dessous est gardé pour mémoire.

- Backend **FastAPI** : `POST /api/refresh` (lance la récupération et renvoie la progression), `GET /api/deals?people=4&when=now`, `GET/POST/DELETE /api/favorites`, `GET/PUT /api/settings`.
- Interface **une seule page**, épurée :
  - **En-tête** : zone (résumé), sélecteur de personnes **1 à 6**, « maintenant / à HH:MM », bouton **Actualiser** avec barre de progression (la récupération dure plusieurs minutes).
  - **Résultats** : cartes affichant le nom, le prix par personne en gros, le total, la composition, les promos, la note, le délai, l'horaire et un lien « Commander sur Uber Eats ».
  - **Réglages** (panneau repliable) : seuil par personne, frais estimés, pondérations, définition d'un repas.
- Le changement du nombre de personnes ou des filtres recalcule à partir du cache, sans requête vers Uber.

**Validé quand** : on lance l'appli, on choisit 3 personnes, on voit des offres, puis on passe à 5 personnes et le résultat change instantanément.

## Phase 4 — Zone géographique
- Choix sur une **carte (Leaflet + OpenStreetMap)** : un point central (clic sur la carte ou recherche d'adresse) et un **rayon** (curseur, par ex. de 0,5 à 5 km), affiché sous forme de cercle.
- Filtrage par distance (formule de haversine) entre le point central et `mapMarker` (latitude/longitude) de chaque restaurant.
- Le flux Uber dépend de l'adresse de livraison. Pour une grande zone, il faut **plusieurs points de requête** (grille espacée d'environ 2 km) et dédoublonner les restaurants. Attention au volume de requêtes : afficher une estimation (« ~6 min, ~120 requêtes »).
- Recherche d'adresse : `mapsSearchV1` d'Uber (à explorer) ou Nominatim (OSM, avec respect de sa politique : 1 requête/s et un user-agent identifié).
- Plusieurs zones enregistrables (« Maison », « Fac »…).

**Validé quand** : un rayon de 1 km donne moins de restaurants qu'un rayon de 3 km, et tous sont bien dans le cercle sur la carte.

## Phase 5 — Favoris ✅ (27/10/2026)
> Fait : `favorites` dans config.yaml (toujours récupérés, même hors flux) ; dans l'appli, étoile sur chaque offre, onglet Favoris (meilleur panier même au-dessus du budget, ou raison de l'absence) et recherche par nom. Détails dans CONTEXT.md. Plan d'origine :

- Fichier `data/favorites.json` (uuid, nom, URL). Ajout via une ★ sur une carte de résultat ou en collant l'URL Uber Eats d'un restaurant (l'uuid est extrait de l'URL).
- Les favoris sont **toujours analysés** : ils passent outre le pré-filtre et ne comptent pas dans `max_stores`.
- Dans l'interface, une section **« Mes favoris »** en tête, qui affiche toujours la meilleure offre de chaque favori, même au-dessus de 10 €/pers (dans ce cas en grisé, avec le prix) ; ou bien un bonus de score. **À trancher avec l'utilisateur.**
- Un favori fermé ou hors zone est signalé (« fermé jusqu'à 18h30 », « hors zone »).

**Validé quand** : un favori classé au-delà des 40 premiers restaurants du flux apparaît bien en tête.

## Phase 6 — Portabilité
**6a. PC (Windows)**
- Paquet **PyInstaller** (un dossier zippé, ou un `.exe` + dossier) : double-clic → le serveur démarre → le navigateur s'ouvre sur l'interface.
- Playwright utilise **Microsoft Edge, déjà installé sur tout Windows** (`channel="msedge"`), ce qui évite d'embarquer Chromium (environ 150 Mo). Repli : télécharger Chromium au premier lancement.
- Données utilisateur (favoris, zones, réglages, cache) dans `%APPDATA%\DailyUber`, pour que la mise à jour de l'appli ne les efface pas.
- Distribution : zip sur GitHub Releases.

**6b. Téléphone** ✅ (27/10/2026, emmagmzc) : fait avec l'option 3 ci-dessous (recherche quotidienne sur GitHub Actions, appli sur GitHub Pages), sans PC allumé. Options envisagées, par ordre de simplicité :
1. **Réseau local** : le PC sert l'interface sur le Wi-Fi, et le téléphone l'ouvre en scannant un QR code affiché par l'appli. Interface installable comme **PWA** (icône sur l'écran d'accueil).
2. **Hors de la maison** : Tailscale (VPN perso gratuit) entre le téléphone et le PC.
3. **Sans PC allumé** : snapshot quotidien publié (GitHub Actions → GitHub Pages en JSON) et lu par la PWA. Risque de blocage des IP GitHub, à tester.

**Validé quand** : 6a, l'appli tourne sur un autre PC Windows sans Python installé ; 6b, l'interface s'utilise depuis le téléphone.

## Phase 7 — Notifications quotidiennes (demande initiale, en partie faite)
> La recherche quotidienne existe (GitHub Actions vers 10 h, Paris). Reste le message Discord.
- Webhook Discord : embeds (restaurant, panier, prix, promo, note, délai, lien), alerte en cas d'échec, mention « 🆕 » via `history.json`.
- Planification à 11h00, heure de Paris : Planificateur de tâches Windows (si le PC est allumé) ou GitHub Actions (crons `0 9 * * *` et `0 10 * * *`, avec un test « est-il 11h à Paris ? » dans le script). Le nombre de personnes et la zone du message viennent des réglages enregistrés.
- README : installation, configuration, déploiement.

---

## Questions ouvertes pour l'utilisateur
1. ~~**Favoris au-dessus de 10 €/pers**~~ : tranché le 27/10, ils sont affichés quand même, prix grisé « au-dessus de X € ».
2. **Zone** : un cercle (centre + rayon) suffit-il, ou faut-il pouvoir dessiner une zone libre ?
3. ~~**Téléphone hors de chez toi**~~ : réglé par GitHub Pages (accessible partout).
4. **Discord quotidien** : toujours souhaité une fois l'interface disponible ?
5. **Calibrage des frais** : peux-tu fournir le détail d'une vraie commande (frais de service, livraison) pour remplacer l'estimation à 10 % ?
