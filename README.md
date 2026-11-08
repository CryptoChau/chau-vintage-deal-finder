# CHAU Vintage Deal Finder

Durchsucht eBay.ch (Sofort-Kaufen) automatisch alle 2 Stunden nach rohen Pokemon-Einzelkarten
aus allen Aeren von 1996 bis 2016 (WOTC, e-Card, EX-Aera inkl. Gold Stars, Diamond & Pearl,
Platinum, HGSS, Black & White, XY; englisch + japanisch; Holos, Gold Stars, beliebte und
seltene Karten) und postet Angebote, die deutlich unter dem Median (aktuelle Angebote plus
Preishistorie) liegen, mit Foto und Link in Discord (`#ebay-vintage-deals`). Gold Stars
kommen zuerst. PSA-gegradete Karten gehoeren in den PSA-Bot.

Laeuft komplett in der Cloud via GitHub Actions - keine Abhaengigkeit vom lokalen PC.
Details siehe `vintage_deal_finder.py` (Docstring) und `.github/workflows/deal-finder.yml`.

## Booster-Pack-Deal-Finder

Zweiter Bot, sucht auf eBay.ch UND Ricardo.ch nach einzelnen versiegelten Booster-Packs
(Vintage bis moderne Hit-Sets) und postet in `#booster-pack-deals`. Details siehe
`booster_deal_finder.py` und `.github/workflows/booster-finder.yml`.

## PSA-Vintage-Deal-Finder

Dritter Bot, sucht auf eBay.ch ausschliesslich nach PSA-gegradeten (PSA 1-10) Pokemon-Einzelkarten
aus allen Aeren von 1996 bis 2016 (WOTC, e-Card, EX-Aera inkl. Gold Stars, Diamond & Pearl,
Platinum, HGSS, Black & White, XY; Holos, Reverse Holos, Gold Stars, beliebte und seltene Karten),
Preisfenster 20-200 CHF. Die Erkennung (Sets/Aeren, Namen, Varianten) kommt aus
`vintage_deal_finder.py`. Der exakte PSA-Grade ist Teil des Vergleichsschluessels. Postet Ideen,
die deutlich unter den anderen aktuellen Angeboten liegen, in `#psa-vintage-deals` - ausdruecklich
KEIN Marktwert (eBay-Verkaufspreise brauchen Login), bitte in Collectr gegenpruefen. Details siehe
`psa_vintage_deal_finder.py` und `.github/workflows/psa-finder.yml`.
