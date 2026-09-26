# CHAU Vintage Deal Finder

Durchsucht eBay.ch (Sofort-Kaufen) automatisch alle 2 Stunden nach Vintage-Pokemon-Karten
(Base Set, Jungle, Fossil, Team Rocket, Gym, Neo; englisch + japanisch; roh oder PSA/BGS/CGC)
und postet Angebote, die deutlich unter dem aktuellen Marktmedian liegen, mit Foto und Link
in Discord (`#ebay-vintage-deals`).

Laeuft komplett in der Cloud via GitHub Actions - keine Abhaengigkeit vom lokalen PC.
Details siehe `vintage_deal_finder.py` (Docstring) und `.github/workflows/deal-finder.yml`.
