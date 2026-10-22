from dataclasses import dataclass, field


@dataclass
class Item:
    title: str
    price: float                 # prix affiché, remises % déjà appliquées par Uber
    section: str = ""
    original_price: float | None = None
    bogo_buy: int = 0            # "1 acheté = 1 offert" -> buy=1, get=1
    bogo_get: int = 0
    bogo_max: int = 0            # nombre max de fois où la promo s'applique (0 = illimité)
    has_customizations: bool = False


@dataclass
class ThresholdPromo:
    min_subtotal: float
    pct_off: float = 0.0         # 0.20 pour -20 %
    amount_off: float = 0.0      # 5.0 pour -5 €
    label: str = ""


@dataclass
class Store:
    uuid: str
    title: str
    url: str
    rating: float | None = None
    rating_count: int = 0
    eta_min: int | None = None
    eta_max: int | None = None
    delivery_fee: float | None = None    # None = inconnu -> valeur par défaut de la config
    free_delivery_min: float | None = None  # "livraison à 0 € dès X €"
    signposts: list[str] = field(default_factory=list)
    threshold_promos: list[ThresholdPromo] = field(default_factory=list)
    is_open: bool = True                 # accepte les commandes au moment de la récupération
    open_until: str = ""                 # "Ouvert jusqu'à 22:00"
    hours: dict[int, list[tuple[int, int]]] | None = None             # voir hours.parse_hours
    delivery_slots: dict[str, list[tuple[int, int]]] = field(default_factory=dict)
    items: list[Item] = field(default_factory=list)


@dataclass
class Basket:
    store: Store
    people: int
    lines: list[tuple[Item, int]]        # (article, quantité payée + offerte)
    items_subtotal: float                # après "1 acheté = 1 offert"
    threshold_discount: float
    delivery_fee: float
    service_fee: float
    small_order_fee: float
    promos_used: list[str]
    score: float = 0.0
    is_new: bool = False
    availability: str = ""               # "Ouvert jusqu'à 22:00" ou "Créneau 12:30–13:00"

    @property
    def total(self) -> float:
        return round(self.items_subtotal - self.threshold_discount + self.delivery_fee
                     + self.service_fee + self.small_order_fee, 2)

    @property
    def per_person(self) -> float:
        return round(self.total / self.people, 2)

    @property
    def key(self) -> str:
        return f"{self.store.uuid}|{self.people}|" + ",".join(sorted(f"{i.title}x{q}" for i, q in self.lines))
