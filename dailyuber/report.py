"""Rendu texte des résultats (console)."""
from .models import Basket


def eur(x: float) -> str:
    return f"{x:.2f} €".replace(".", ",")


def format_basket(rank: int, b: Basket) -> str:
    s = b.store
    if not s.eta_min:
        eta = "?"
    else:
        eta = f"{s.eta_min} min" if s.eta_min == s.eta_max else f"{s.eta_min}-{s.eta_max} min"
    rating = f"{s.rating:.1f}★ ({s.rating_count})" if s.rating else "sans note"
    lines = [f"{rank}. {'🆕 ' if b.is_new else ''}{s.title}  —  {eur(b.per_person)}/pers  (total {eur(b.total)})"]
    lines.append(f"   {rating} · {eta}" + (f" · {b.availability}" if b.availability else ""))
    for it, qty in b.lines:
        custom = " *" if it.has_customizations else ""
        lines.append(f"   - {qty} × {it.title} ({eur(it.price)}){custom}")
    fees = f"articles {eur(b.items_subtotal)}"
    if b.threshold_discount:
        fees += f" − remise {eur(b.threshold_discount)}"
    fees += f" + livraison {eur(b.delivery_fee)} + service ~{eur(b.service_fee)}"
    if b.small_order_fee:
        fees += f" + petite commande {eur(b.small_order_fee)}"
    lines.append(f"   {fees}")
    if b.promos_used:
        lines.append(f"   Promos : {' | '.join(dict.fromkeys(b.promos_used))}")
    lines.append(f"   {s.url}")
    return "\n".join(lines)


def format_report(deals: dict[int, list[Basket]], stats: dict) -> str:
    out = [f"=== DailyUber — {stats['address']} ===",
           f"Données du {stats['fetched_at'].replace('T', ' à ')} — "
           f"{stats['feed_stores']} restaurants dans le flux, {stats['fetched']} menus analysés, "
           f"{stats['open']} ouverts à la récupération",
           f"Livraison : {stats['when']}"]
    for people, baskets in deals.items():
        out.append(f"\n--- Top {len(baskets)} pour {people} personnes ---")
        if not baskets:
            out.append("Aucun panier sous le seuil aujourd'hui.")
        out += [format_basket(i, b) for i, b in enumerate(baskets, 1)]
    out.append("\n* article personnalisable : le prix de base peut augmenter selon les options.")
    out.append("Frais de service estimés (non exposés par Uber hors checkout).")
    return "\n".join(out)
