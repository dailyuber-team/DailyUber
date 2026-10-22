# DailyUber

Trouve les meilleurs plans Uber Eats autour d'Aix-en-Provence pour manger de 1 à 6 personnes **à moins de 10 € par personne**, frais compris.

L'outil parcourt les restaurants livrables, lit leurs menus et leurs promos (1 acheté = 1 offert, -20 % dès X €…), compose le panier le moins cher, puis ajoute les frais de livraison et de service. Il affiche le top 5 des offres pour chaque taille de groupe.

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
| `scoring` | Poids du prix, de la note et du délai dans le classement |
| `fetch` | Nombre de restaurants analysés, délai entre les requêtes, durée du cache |

## Tests

```bash
.venv\Scripts\python -m pytest -q
```

## À savoir

- **Les frais de service sont estimés** (10 % du panier, entre 0,99 € et 4,99 €) : Uber ne donne le montant exact qu'au paiement. Le prix réel peut donc varier un peu.
- On ne se connecte jamais à un compte Uber. L'outil lit les données publiques du site, ce que les CGU d'Uber n'autorisent pas : à garder pour un usage perso et modéré.
- **Uber One** : livraison à 0 € et frais de service réduits de 50 % dès 12 € d'articles. Uber annonce « jusqu'à 50 % », donc l'économie réelle peut être un peu plus faible.
- Les promos « article offert » sont signalées mais pas comptées dans le prix.
- Chaque offre indique jusqu'à quand le restaurant est ouvert, ou le créneau de livraison retenu. Pour une commande à l'avance, le menu et les prix sont ceux du moment de la recherche (une formule midi peut apparaître pour le soir).

## Pour aller plus loin

- [ROADMAP.md](ROADMAP.md) : les prochaines étapes (interface graphique, choix de zone, favoris, version téléphone).
- [CONTEXT.md](CONTEXT.md) : les détails techniques.
