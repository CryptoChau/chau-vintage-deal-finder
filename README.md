# CHAU Vintage Deal Finder

Durchsucht eBay.ch (Sofort-Kaufen) automatisch alle 2 Stunden nach Vintage-Pokemon-Karten
(Base Set, Jungle, Fossil, Team Rocket, Gym, Neo; englisch + japanisch; roh oder PSA/BGS/CGC)
und postet Angebote, die deutlich unter dem aktuellen Marktmedian liegen, mit Foto und Link
in Discord (`#ebay-vintage-deals`).

Laeuft komplett in der Cloud via GitHub Actions - keine Abhaengigkeit vom lokalen PC.
Details siehe `vintage_deal_finder.py` (Docstring) und `.github/workflows/deal-finder.yml`.

## Booster-Pack-Deal-Finder

Zweiter Bot, sucht auf eBay.ch UND Ricardo.ch nach einzelnen versiegelten Booster-Packs
(Vintage bis moderne Hit-Sets) und postet in `#booster-pack-deals`. Details siehe
`booster_deal_finder.py` und `.github/workflows/booster-finder.yml`.

## PSA-Vintage-Deal-Finder

Dritter Bot, sucht auf eBay.ch ausschliesslich nach PSA-gegradeten (PSA 1-10) Vintage-
Einzelkarten (gleiche Sets/Aera wie oben). Der exakte PSA-Grade ist zwingender Teil des
Vergleichsschluessels (nie unterschiedliche Grades im selben Preisvergleich mischen) und
postet in `#psa-vintage-deals`. Details siehe `psa_vintage_deal_finder.py` und
`.github/workflows/psa-finder.yml`.
