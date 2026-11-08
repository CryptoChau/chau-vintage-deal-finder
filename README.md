# CHAU Vintage Deal Finder

Durchsucht eBay.ch (Sofort-Kaufen) automatisch alle 2 Stunden nach rohen Pokemon-Einzelkarten
aus allen Aeren von 1996 bis 2016. Seltene Bulbasaur/Bisasam kommen zuerst (eigenes
Roh-Budget CHF 400), danach andere Vintage-Holos. Ein Post entsteht nur bei exakter
Identitaet (Nummer, Set, Ursprungsjahr, Sprache, Finish, Edition, Variante, Rohzustand),
belegtem CH-Gesamtpreis und einem Preis unter dem Median von mindestens drei
oeffentlichen Verkaeufen (90 Tage, sonst 180). Angebotspreise, Preishistorie und
Preisguides sind kein Median. Ohne Treffer bleibt der Kanal still. PSA-Karten gehoeren
in den PSA-Bot.

Laeuft komplett in der Cloud via GitHub Actions - keine Abhaengigkeit vom lokalen PC.
Details siehe `vintage_deal_finder.py` (Docstring) und `.github/workflows/deal-finder.yml`.

## Booster-Pack-Deal-Finder

Zweiter Bot, sucht auf eBay.ch UND Ricardo.ch nach einzelnen versiegelten Booster-Packs
(Vintage bis moderne Hit-Sets) und postet in `#booster-pack-deals`. Details siehe
`booster_deal_finder.py` und `.github/workflows/booster-finder.yml`.

## PSA-Vintage-Deal-Finder

Dritter Bot, sucht auf eBay.ch nach PSA-Einzelkarten bis 2016. Dieselbe Pruefung wie der
Roh-Bot: exakter Grade, belegter Gesamtpreis strikt unter CHF 200, Median aus echten
Verkaeufen. Ohne gelesenes Slab-Label wird nicht gepostet. Details siehe
`psa_vintage_deal_finder.py` und `.github/workflows/psa-finder.yml`.
