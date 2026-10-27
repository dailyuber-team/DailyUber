# DailyUber

Trouve les meilleurs plans Uber Eats autour d'Aix-en-Provence pour manger de 1 à 6 personnes **à moins de 10 € par personne**, frais compris.

L'outil parcourt les restaurants livrables, lit leurs menus et leurs promos (1 acheté = 1 offert, -20 % dès X €…), compose le panier le moins cher, puis ajoute les frais de livraison et de service. Il affiche le top 5 des offres pour chaque taille de groupe.

## L'appli sur téléphone

L'appli est en ligne sur **https://dailyuber-team.github.io/DailyUber/** (une fois GitHub Pages activé, voir plus bas). Elle n'a pas besoin d'un PC allumé :

- chaque jour vers **10 h**, GitHub lance la recherche Uber Eats tout seul, sur **tous** les restaurants bien notés (environ 200, un quart d'heure de recherche), et publie les offres ;
- dans l'appli, on choisit le nombre de personnes, l'heure de livraison, le budget, Uber One et le type de repas : le calcul se fait dans le téléphone, instantanément ;
- le bouton « Commander sur Uber Eats » ouvre le restaurant dans l'appli Uber Eats ;
- l'onglet **Favoris** montre directement tes restaurants préférés (voir plus bas).

**L'installer comme une vraie appli** : sur iPhone, ouvre le lien dans Safari, touche le bouton Partager puis « Sur l'écran d'accueil ». Sur Android, ouvre-le dans Chrome, menu ⋮ puis « Ajouter à l'écran d'accueil ».

Pour relancer une recherche tout de suite : onglet **Actions** du dépôt GitHub, « Recherche du jour et appli », bouton **Run workflow**.

### Mise en place (une seule fois)

1. Rendre le dépôt public : **Settings → General → Danger Zone → Change visibility**.
2. Activer GitHub Pages : **Settings → Pages → Source : GitHub Actions**.
3. Lancer la première recherche : **Actions → Recherche du jour et appli → Run workflow**.

### Logo et icônes

Le logo est dans [design/logo.webp](design/logo.webp). Pour en changer : remplacer ce fichier, puis régénérer toutes les icônes (iPhone, Android, onglet) :

```bash
.venv\Scripts\python tools/make_icons.py
```

### Tester l'appli sur le PC

```bash
.venv\Scripts\python -m dailyuber.export --offline --out web/data.json
```

```bash
.venv\Scripts\python -m http.server 8765 --directory web
```

Puis ouvrir http://localhost:8765 dans le navigateur.

## Favoris

- **Dans l'appli** : touche l'étoile ☆ d'une offre, ou cherche un restaurant par son nom en bas de l'onglet **Favoris**. L'onglet affiche pour chaque favori son meilleur panier avec tes réglages (personnes, heure, Uber One), même s'il dépasse ton budget, ou dit pourquoi il n'y en a pas (fermé, menu pas analysé…). Ces étoiles restent sur ton téléphone.
- **Pour qu'un favori soit vérifié chaque jour** : la recherche automatique n'analyse qu'une quarantaine de restaurants. Ajoute le lien Uber Eats du restaurant dans `favorites` de [config.yaml](config.yaml) : il sera toujours analysé, et marqué « Suivi chaque jour » dans l'appli.

```yaml
favorites:
  - https://www.ubereats.com/fr/store/pekyo/o1RM0wMxRn6Xy4keWIR-AA
```

## Installation

Il faut Python 3 sous Windows, lancé avec la commande `py`.

```bash
py -m venv .venv
```

```bash
.venv\Scripts\pip install -r requirements.txt
```

```bash
.venv\Scripts\playwright install chromium
```

## Utilisation

Lancer une recherche (2 et 4 personnes par défaut) :

```bash
.venv\Scripts\python -m dailyuber.main
```

Choisir le nombre de personnes (de 1 à 6, plusieurs valeurs possibles) :

```bash
.venv\Scripts\python -m dailyuber.main --people 1 3 6
```

Chercher pour plus tard (seuls les restaurants avec un créneau de livraison à cette heure sont gardés) :

```bash
.venv\Scripts\python -m dailyuber.main --at 12:30 --on demain
```

La première recherche prend environ 3 minutes. Les données sont ensuite gardées en cache pendant 30 minutes : les recherches suivantes sont instantanées.

| Option | Effet |
|---|---|
| `--people N …` | Nombre(s) de personnes, de 1 à 6 |
| `--at HH:MM` | Heure de livraison visée : aujourd'hui, ou demain si l'heure est passée (par défaut : maintenant) |
| `--on JOUR` | Jour visé avec `--at` : `demain` ou une date `AAAA-MM-JJ` |
| `--uber-one` / `--no-uber-one` | Calcule les prix avec ou sans l'abonnement Uber One |
| `--type TYPE …` | Seulement certains types de repas : `burger`, `crousty`, `pizza`, `sushi`, `asiatique`, `poke`, `tacos_kebab`, `sandwich`, `salade`, `pates`, `poulet` |
| `--budget EUR` | Prix max par personne (par défaut : 10 €) |
| `--sort rating` | Classe les mieux notés d'abord, puis les moins chers (par défaut : `score`, qui mélange prix, note et délai) ; l'appli classe toujours par note |
| `--refresh` | Force une nouvelle récupération, même si le cache est récent |
| `--offline` | Réutilise les dernières données, même si elles sont anciennes |
| `--config chemin` | Utilise un autre fichier de configuration |

## Réglages

Tout se règle dans [config.yaml](config.yaml) :

| Section | À quoi ça sert |
|---|---|
| `address` | Adresse de livraison (par défaut : Cours Mirabeau) |
| `selection` | Nombres de personnes par défaut, budget max par personne, taille du top, plats différents imposés ou non |
| `prefilter` | Note minimale et délai de livraison maximal |
| `fees` | Estimation des frais de livraison et de service, et Uber One (`uber_one.enabled: true` si tu es abonné) |
| `meal` | Ce qui compte comme un « plat » (prix minimum, mots-clés) |
| `meal_types` | Les types de repas proposés et leurs mots-clés |
| `scoring` | Poids du prix, de la note et du délai dans le classement |
| `fetch` | Nombre de restaurants analysés (40 sur le PC ; `0` = tous), délai entre les requêtes, durée du cache |
| `favorites` | Restaurants toujours analysés et affichés dans l'onglet Favoris (liens Uber Eats) |

## Tests

```bash
.venv\Scripts\python -m pytest -q
```

L'appli refait le calcul en JavaScript ([web/engine.js](web/engine.js)) : toute modification de `selection.py` ou `hours.py` doit y être reportée. Le test `tests/test_parity.py` (il faut [Node.js](https://nodejs.org)) vérifie que les deux donnent exactement les mêmes offres.

## À savoir

- **Les frais de service sont estimés** (10 % du panier, entre 0,99 € et 4,99 €) : Uber ne donne le montant exact qu'au paiement. Le prix réel peut donc varier un peu.
- On ne se connecte jamais à un compte Uber. L'outil lit les données publiques du site, ce que les CGU d'Uber n'autorisent pas : à garder pour un usage perso et modéré.
- **Uber One** : livraison à 0 € et frais de service réduits de 50 % dès 12 € d'articles. Uber annonce « jusqu'à 50 % », donc l'économie réelle peut être un peu plus faible.
- Les promos « article offert » sont signalées mais pas comptées dans le prix.
- Chaque offre indique jusqu'à quand le restaurant est ouvert, ou le créneau de livraison retenu. Pour une commande à l'avance, le menu et les prix sont ceux du moment de la recherche (une formule midi peut apparaître pour le soir).

## Pour aller plus loin

- [ROADMAP.md](ROADMAP.md) : les prochaines étapes (choix de zone, message Discord…).
- [CONTEXT.md](CONTEXT.md) : les détails techniques.
