# -*- coding: utf-8 -*-
"""
CHAU Vintage-Deal-Finder (Karten bis 2016)
==========================================
Durchsucht eBay.ch (Sofort-Kaufen) nach rohen (ungegradeten) Pokemon-Einzelkarten aus ALLEN
Aeren von 1996 bis 2016: WOTC (Base Set bis Neo Destiny), e-Card (Aquapolis/Skyridge/
Expedition), EX-Aera inkl. GOLD STARS (Ruby & Sapphire bis Power Keepers), Diamond & Pearl,
Platinum, HeartGold SoulSilver, Black & White, XY (bis XY Evolutions, Nov. 2016); englisch +
japanisch. Gesucht werden Holos, Gold Stars, beliebte und seltene Karten.

Pro Karte (Set + Name + Variante + Zustand) wird ein Referenzpreis gebildet: Median aller
ANDEREN aktuellen Angebote PLUS Preishistorie frueherer Laeufe (seltene Karten haben selten
3+ Angebote gleichzeitig, siehe Learnings im Booster-Pack-Finder). Gemeldet werden Angebote
deutlich UNTER diesem Median (= Verkaeufer hat den Preis nicht dem Markt nachgezogen) in Discord
(Webhook DISCORD_WEBHOOK_HOLO, Kanal #ebay-vintage-deals) - mit Foto und Link.

Reihenfolge der Meldungen: Gold Stars zuerst, dann beliebte Pokemon in seltener Variante
(Holo/ex/Full Art/LV.X/Prime/LEGEND/BREAK/Secret...), dann der Rest; innerhalb einer Stufe
japanische Verkaeufer zuerst, danach groesster Abstand zum Median.

PSA-gegradete Karten sind bewusst NICHT hier (eigener Bot/Kanal #psa-vintage-deals), BGS/CGC
bleiben im Pool (Grade ist Teil des Vergleichsschluessels).

Zielgruppe: Flohmarkt-Kunde, der Vintage-Holos fuer 100-300 CHF pro Karte kauft -> Einkaufs-
fenster 20-250 CHF inkl. Versand (MIN_TOTAL / MAX_TOTAL), Marktwert (Median) mind. 40 CHF.

Haendler-Herkunft: kein Land wird ausgeschlossen (zu hohe Versandkosten fangen MIN/MAX_TOTAL
ab). Japanische Verkaeufer werden bevorzugt und mit Flagge markiert.

Aufruf:
    python vintage_deal_finder.py              -> normaler Lauf, postet neue Deals
    python vintage_deal_finder.py --dry-run    -> nur Log/Ausgabe, kein Discord, kein State
    python vintage_deal_finder.py --reset      -> vergisst bereits gemeldete Angebote
    VINTAGE_QUERY_FILTER="gold star,xy"        -> nur Suchen, die einen dieser Teilstrings
                                                  enthalten (schnelle Tests)

Technik: eBay blockt Headless-Browser (403). Es wird deshalb ein echtes Chromium-Fenster
per Playwright gestartet (lokal ausserhalb des Bildschirms, in der Cloud unter xvfb).
"""
import json
import os
import re
import statistics
import string
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(HERE, "deal_state.json")
LOG_FILE = os.path.join(HERE, "deal_finder.log")
LOCK_FILE = os.path.join(HERE, "deal_finder.lock")
NAMES_FILE = os.path.join(HERE, "pokemon_names.json")

DRY_RUN = "--dry-run" in sys.argv
RESET = "--reset" in sys.argv

# ---------------------------------------------------------------- Einstellungen
MIN_TOTAL = 20.0        # CHF inkl. Versand (Karten ab ca. 2003 sind guenstiger als WOTC-Holos)
MAX_TOTAL = 250.0       # CHF inkl. Versand (Kunde zahlt 100-300, Marge muss drin sein)
MAX_TOTAL_GOLD = 600.0  # Gold Stars kosten roh meist 300-1500 CHF - mit 250 CHF faellt jede echte durchs Fenster
DEAL_RATIO = 0.72       # Preis <= 72 % des Medians der ANDEREN Angebote gleicher Karte/Variante
MIN_SAMPLES = 4         # mind. so viele ANDERE Vergleichsangebote (inkl. Historie); Gold Stars: MIN_SAMPLES_GOLD
MIN_SAMPLES_GOLD = 3
DEAL_RATIO_THIN = 0.62  # bei weniger als 8 Vergleichsangeboten strengere Schwelle
MIN_MEDIAN = 40.0       # Marktwert (Median) mind. so hoch, sonst fuer den 100-300-CHF-Kunden uninteressant
MAX_POSTS_PER_RUN = 15
PAGE_WAIT_MS = 2500
HISTORY_MAX_DAYS = 60       # so lange bleibt ein nicht mehr gesehenes Angebot als Preisreferenz erhalten
HISTORY_MAX_PER_KEY = 30    # pro Karte hoechstens so viele Historien-Eintraege (haelt deal_state.json klein)

# Schluesselwoerter, die moderne Karten (ab 2017) ausschliessen. Die Set-Whitelist (SETS) ist
# der eigentliche Filter - das hier faengt Faelle ab, in denen ein modernes Angebot zufaellig
# einen alten Set-Namen enthaelt ("Sun & Moon Base Set") oder gar keinen Set-Namen hat.
EXCL_MODERN_QUERY = " -vmax -vstar -gx -paldea -scarlet -violet -sword -shield"

SEARCHES = [
    # ---- WOTC (Base Set - Neo Destiny), englisch
    "pokemon base set holo", "pokemon base set 1st edition holo", "pokemon shadowless holo",
    "pokemon jungle holo", "pokemon fossil holo", "pokemon team rocket holo",
    "pokemon gym heroes holo", "pokemon gym challenge holo",
    "pokemon neo genesis holo", "pokemon neo discovery holo", "pokemon neo revelation holo", "pokemon neo destiny holo",
    "pokemon wotc holo rare", "pokemon shining holo",
    # ---- WOTC japanisch
    "pokemon japanese base set holo", "pokemon japanese jungle holo", "pokemon japanese fossil holo",
    "pokemon japanese team rocket holo", "pokemon japanese gym holo", "pokemon japanese neo holo",
    "pokemon japanese no rarity holo", "pokemon japanese old back holo",
    # ---- e-Card-Aera (2002-2003)
    "pokemon aquapolis holo", "pokemon skyridge holo", "pokemon expedition holo", "pokemon legendary collection holo",
    # ---- Gold Stars (EX-Aera) - hoechste Prioritaet
    "pokemon gold star", "pokemon gold star holo", "pokemon charizard gold star", "pokemon rayquaza gold star",
    "pokemon mew gold star", "pokemon umbreon gold star", "pokemon espeon gold star", "pokemon pikachu gold star",
    # ---- EX-Aera (2003-2007)
    "pokemon ex ruby sapphire holo", "pokemon ex sandstorm holo", "pokemon ex dragon holo",
    "pokemon team magma team aqua holo", "pokemon ex hidden legends holo", "pokemon ex firered leafgreen holo",
    "pokemon ex team rocket returns holo", "pokemon ex deoxys holo", "pokemon ex emerald holo",
    "pokemon ex unseen forces holo", "pokemon ex delta species holo", "pokemon ex legend maker holo",
    "pokemon ex holon phantoms holo", "pokemon ex crystal guardians holo", "pokemon ex dragon frontiers holo",
    "pokemon ex power keepers holo",
    # ---- Diamond & Pearl / Platinum / HeartGold SoulSilver (2007-2011)
    "pokemon diamond pearl holo lv.x", "pokemon mysterious treasures secret wonders holo",
    "pokemon great encounters majestic dawn holo", "pokemon legends awakened holo", "pokemon stormfront holo",
    "pokemon platinum holo lv.x", "pokemon rising rivals supreme victors holo",
    "pokemon heartgold soulsilver holo prime", "pokemon unleashed holo", "pokemon undaunted triumphant holo",
    "pokemon call of legends shiny",
    # ---- Black & White (2011-2013)
    "pokemon black white holo ex", "pokemon emerging powers noble victories holo", "pokemon next destinies holo",
    "pokemon dark explorers holo", "pokemon dragons exalted holo", "pokemon boundaries crossed holo",
    "pokemon plasma storm holo", "pokemon plasma freeze holo", "pokemon plasma blast holo",
    "pokemon legendary treasures holo", "pokemon radiant collection", "pokemon dragon vault holo",
    # ---- XY (2014-2016)
    "pokemon xy holo ex", "pokemon flashfire holo", "pokemon furious fists holo", "pokemon phantom forces holo",
    "pokemon primal clash holo", "pokemon double crisis roaring skies holo", "pokemon ancient origins holo",
    "pokemon breakthrough holo", "pokemon breakpoint holo", "pokemon fates collide holo",
    "pokemon steam siege holo", "pokemon generations holo", "pokemon xy evolutions holo",
    # ---- Japanisch, jenseits von WOTC
    "pokemon japanese gold star", "pokemon japanese ex holo", "pokemon japanese pcg holo", "pokemon japanese adv holo",
    "pokemon japanese e-card holo", "pokemon japanese diamond pearl holo", "pokemon japanese legend holo",
    "pokemon japanese xy holo", "pokemon japanese bw holo",
    # ---- Beliebte Karten ueber alle Aeren (Negativ-Woerter halten moderne Treffer fern)
    "pokemon charizard ex holo" + EXCL_MODERN_QUERY, "pokemon pikachu ex holo" + EXCL_MODERN_QUERY,
    "pokemon mewtwo ex holo" + EXCL_MODERN_QUERY, "pokemon rayquaza ex holo" + EXCL_MODERN_QUERY,
    "pokemon lugia ex holo" + EXCL_MODERN_QUERY, "pokemon umbreon holo" + EXCL_MODERN_QUERY,
    "pokemon espeon holo" + EXCL_MODERN_QUERY, "pokemon gengar holo ex" + EXCL_MODERN_QUERY,
    "pokemon mega charizard ex" + EXCL_MODERN_QUERY, "pokemon eevee evolution ex holo" + EXCL_MODERN_QUERY,
]

# Optionaler Filter fuer schnelle Tests (z.B. VINTAGE_QUERY_FILTER="gold star,flashfire")
_qf = [s.strip().lower() for s in os.environ.get("VINTAGE_QUERY_FILTER", "").split(",") if s.strip()]
if _qf:
    SEARCHES = [q for q in SEARCHES if any(f in q.lower() for f in _qf)]

# (Name, Regex, Aera) - REIHENFOLGE ZAEHLT: spezifische Sets vor generischen. EX-Aera-Sets tragen
# optional das Praefix "EX", damit es beim Entfernen des Set-Namens aus dem Titel mit verschwindet
# (sonst wuerde "EX Dragon Frontiers" jede Karte als "EX"-Karte markieren).
_EX = r"(?:\bex[\s\-]*)?"
SETS = [
    ("Base Set 1st Ed.", r"\b1st\s*ed(ition)?\b.*\bbase\b|\bbase\b.*\b1st\s*ed(ition)?\b", "WOTC"),
    ("Base Set Shadowless", r"shadowless", "WOTC"),
    ("No Rarity (JP)", r"no\s*rarit", "WOTC"),
    # ---- EX-Aera (vor "Team Rocket", sonst frisst dessen Regex "Team Rocket Returns")
    ("EX Team Rocket Returns", _EX + r"team\s*rocket\s*returns", "EX"),
    ("EX Ruby & Sapphire", _EX + r"ruby\s*(?:&|and)?\s*sapphire", "EX"),
    ("EX Sandstorm", _EX + r"sandstorm", "EX"),
    ("EX Dragon Frontiers", _EX + r"dragon\s*frontiers", "EX"),
    ("EX Dragon", r"\bex\s*dragon\b(?!\s*frontiers)", "EX"),
    ("EX Team Magma vs Team Aqua", _EX + r"(?:team\s*)?(?:magma|aqua)\s*(?:vs\.?|versus|&|and)\s*(?:team\s*)?(?:aqua|magma)", "EX"),
    ("EX Hidden Legends", _EX + r"hidden\s*legends", "EX"),
    ("EX FireRed & LeafGreen", _EX + r"fire\s*red\s*(?:&|and)?\s*leaf\s*green|\bfrlg\b", "EX"),
    ("EX Deoxys", r"\bex\s*deoxys\b", "EX"),
    ("XY Emerald Break", r"emerald\s*break", "XY"),
    ("EX Emerald", _EX + r"\bemerald\b", "EX"),
    ("EX Unseen Forces", _EX + r"unseen\s*forces", "EX"),
    ("EX Delta Species", _EX + r"delta\s*species", "EX"),
    ("EX Legend Maker", _EX + r"legend\s*maker", "EX"),
    ("EX Holon Phantoms", _EX + r"holon\s*phantoms", "EX"),
    ("EX Crystal Guardians", _EX + r"crystal\s*guardians", "EX"),
    ("EX Power Keepers", _EX + r"power\s*keepers", "EX"),
    # ---- e-Card-Aera
    ("Aquapolis", r"\baquapolis\b", "e-Card"),
    ("Skyridge", r"\bskyridge\b", "e-Card"),
    ("Expedition", r"\bexpedition\b", "e-Card"),
    ("Legendary Collection", r"legendary\s*collection", "WOTC"),
    # ---- Diamond & Pearl / Platinum
    ("DP Mysterious Treasures", r"mysterious\s*treasures", "DP/Pt"),
    ("DP Secret Wonders", r"secret\s*wonders", "DP/Pt"),
    ("DP Great Encounters", r"great\s*encounters", "DP/Pt"),
    ("DP Majestic Dawn", r"majestic\s*dawn", "DP/Pt"),
    ("DP Legends Awakened", r"legends?\s*awakened", "DP/Pt"),
    ("DP Stormfront", r"storm\s*front", "DP/Pt"),
    ("Platinum Rising Rivals", r"rising\s*rivals", "DP/Pt"),
    ("Platinum Supreme Victors", r"supreme\s*victors", "DP/Pt"),
    # ---- HeartGold SoulSilver
    ("HGSS Unleashed", r"\bunleashed\b", "HGSS"),
    ("HGSS Undaunted", r"\bundaunted\b", "HGSS"),
    ("HGSS Triumphant", r"\btriumphant\b", "HGSS"),
    ("HGSS Call of Legends", r"call\s*of\s*legends", "HGSS"),
    # ---- Black & White
    ("BW Emerging Powers", r"emerging\s*powers", "BW"),
    ("BW Noble Victories", r"noble\s*victories", "BW"),
    ("BW Next Destinies", r"next\s*destinies", "BW"),
    ("BW Dark Explorers", r"dark\s*explorers", "BW"),
    ("BW Dragons Exalted", r"dragons?\s*exalted", "BW"),
    ("BW Dragon Vault", r"dragon\s*vault", "BW"),
    ("BW Boundaries Crossed", r"boundaries\s*crossed", "BW"),
    ("BW Plasma Storm", r"plasma\s*storm", "BW"),
    ("BW Plasma Freeze", r"plasma\s*freeze", "BW"),
    ("BW Plasma Blast", r"plasma\s*blast", "BW"),
    ("BW Legendary Treasures", r"legendary\s*treasures", "BW"),
    ("BW Radiant Collection", r"radiant\s*collection", "BW"),
    # ---- XY
    ("XY Flashfire", r"flash\s*fire", "XY"),
    ("XY Furious Fists", r"furious\s*fists", "XY"),
    ("XY Phantom Forces", r"phantom\s*forces", "XY"),
    ("XY Primal Clash", r"primal\s*clash", "XY"),
    ("XY Double Crisis", r"double\s*crisis", "XY"),
    ("XY Roaring Skies", r"roaring\s*skies", "XY"),
    ("XY Ancient Origins", r"ancient\s*origins", "XY"),
    ("XY BREAKthrough", r"break\s*through", "XY"),
    ("XY BREAKpoint", r"break\s*point", "XY"),
    ("XY Fates Collide", r"fates\s*collide", "XY"),
    ("XY Steam Siege", r"steam\s*siege", "XY"),
    ("XY Generations", r"\bgenerations\b", "XY"),
    ("XY Evolutions", r"\bevolutions\b", "XY"),
    ("POP Series", r"\bpop\s*series\b|\bpop\s*\d\b", "EX"),
    # Promos VOR den generischen Serien-Namen (sonst wird "XY Black Star Promo" als Set "XY" erkannt
    # und dieselbe Karte landet in zwei Toepfen); der Promo-Code (XY69, DP47, ...) wird Teil des
    # Set-Namens, weil z.B. Rayquaza EX XY69 (Shiny) und XY73 voellig verschiedene Preise haben.
    ("JP Promo", r"\d{1,3}\s*/\s*(?:xy|bw|dpt?|hgss|adv|pcg|l)\s*-?\s*p\b", "Promo"),
    ("Black Star Promo", r"black\s*star\s*promo|promo\s*black\s*star|\b(?:xy|bw|dp|hgss)\s*-?\s*\d{2,3}(?!\s*/)\b", "Promo"),
    # ---- generische Serien-Namen (erst nach allen spezifischen Sets und Promos)
    ("Platinum", r"\bplatinum\b", "DP/Pt"),
    ("Diamond & Pearl", r"diamond\s*(?:&|and)?\s*pearl", "DP/Pt"),
    ("HeartGold SoulSilver", r"heart\s*gold\s*(?:&|and)?\s*soul\s*silver|\bhgss\b", "HGSS"),
    ("Black & White", r"black\s*(?:&|and)?\s*white\b", "BW"),
    ("XY", r"\bxy\b", "XY"),
    # ---- WOTC-Rest / japanische Serien (generischer, deshalb ganz hinten)
    ("Team Rocket", r"team\s*rocket|\brocket\b", "WOTC"),
    ("Gym Heroes", r"gym\s*heroes", "WOTC"),
    ("Gym Challenge", r"gym\s*challenge", "WOTC"),
    ("Gym", r"\bgym\b|leaders' stadium|challenge from the darkness", "WOTC"),
    ("Neo Genesis", r"neo\s*genesis|gold,? silver,? to a new world", "WOTC"),
    ("Neo Discovery", r"neo\s*discovery|crossing the ruins", "WOTC"),
    ("Neo Revelation", r"neo\s*revelation|awakening legends", "WOTC"),
    ("Neo Destiny", r"neo\s*destiny|darkness and to light", "WOTC"),
    ("Neo Premium File", r"premium\s*file", "WOTC"),
    ("Neo", r"\bneo\b", "WOTC"),
    ("Jungle", r"\bjungle\b", "WOTC"),
    ("Fossil", r"\bfossil\b|mystery of the fossils", "WOTC"),
    ("Base Set 2", r"base\s*set\s*2", "WOTC"),
    ("Base Set", r"\bbase\s*set\b|\bbase\b|expansion pack", "WOTC"),
    ("e-Card (JP)", r"\be[\-\s]?card\b|pokemon\s*card\s*e\b", "e-Card"),
    ("ADV (JP)", r"\badv\b", "EX"),
    ("PCG (JP)", r"\bpcg\b", "EX"),
    ("Vending/Promo (JP)", r"vending|\bpromo\b|corocoro", "Promo"),
]
SETS_C = [(n, re.compile(p, re.I), era) for n, p, era in SETS]

# Namen, die nicht (oder anders) in der Gen-1-6-Pokedex-Liste stehen: Trainer-/Rocket-Karten
# der WOTC-Aera, die als eigene "Karte" gelten sollen.
EXTRA_NAMES = [
    "Here Comes Team Rocket", "Rocket's Sneak Attack", "Rainbow Energy", "Lucky Stadium",
]
POPULAR = {
    "charizard", "pikachu", "mew", "mewtwo", "rayquaza", "lugia", "ho-oh", "umbreon", "espeon", "gengar",
    "blastoise", "venusaur", "dragonite", "gyarados", "eevee", "vaporeon", "jolteon", "flareon", "glaceon",
    "leafeon", "sylveon", "tyranitar", "suicune", "raikou", "entei", "celebi", "latias", "latios", "groudon",
    "kyogre", "deoxys", "garchomp", "lucario", "darkrai", "arceus", "giratina", "dialga", "palkia",
    "reshiram", "zekrom", "kyurem", "greninja", "gardevoir", "blaziken", "sceptile", "swampert", "machamp",
    "alakazam", "snorlax", "lapras", "articuno", "zapdos", "moltres", "absol", "salamence", "metagross",
    "jirachi", "manaphy", "shaymin", "victini", "zoroark", "xerneas", "yveltal", "mimikyu", "togepi",
    "scizor", "steelix", "typhlosion", "feraligatr", "meganium", "ampharos", "houndoom", "crobat", "kingdra",
}


def norm(t):
    """Kleinschreibung, Akzente weg (Flabébé -> flabebe), typografische Apostrophe vereinheitlicht.
    Die Symbole fuer Gold Star / Delta bleiben erhalten."""
    t = (t or "").replace("’", "'").replace(" ", " ")
    t = "".join(ch for ch in unicodedata.normalize("NFKD", t) if not unicodedata.combining(ch))
    return t.lower()


def _load_names():
    names = []
    try:
        names = json.load(open(NAMES_FILE, encoding="utf-8"))
    except Exception:
        pass
    # Fallback/Ergaenzung: die klassischen Vintage-Holos (falls die JSON-Datei fehlt)
    names += ["Charizard", "Blastoise", "Venusaur", "Pikachu", "Mewtwo", "Mew", "Gengar", "Dragonite", "Lugia",
              "Umbreon", "Espeon", "Rayquaza", "Raichu", "Alakazam", "Machamp", "Gyarados"]
    return names


def _build_name_regex(names):
    base = {}
    for n in names:
        n2 = norm(re.sub(r"[♀♂]", "", n)).strip()
        if len(n2) < 3:
            continue
        base[n2] = n2
    for n in EXTRA_NAMES:
        base[norm(n)] = norm(n)
    parts = []
    for n2 in sorted(base, key=len, reverse=True):
        # Trenner (Leerzeichen, Bindestrich, Punkt, Apostroph) im Namen optional
        pat = re.escape(n2)
        pat = re.sub(r"(?:\\ |\\-|\\\.|')", r"[\\s\\-.']*", pat)
        parts.append(pat)
    return re.compile(r"(?<![a-z0-9])(" + "|".join(parts) + r")(?![a-z0-9])")


NAME_RE = _build_name_regex(_load_names())
PREFIX_RE = re.compile(r"(dark|light|shining|holon'?s?|rocket'?s?|team\s+\w+'?s?|(?:lt\.?\s+)?[a-z]+'s)\s*$")

EXCLUDE = re.compile(
    r"\b(proxy|custom|fake|replica|orica|repro|lot\b|bundle|sammlung|collection of|"
    r"x\s?\d{1,2}(?!\s*/)\b|\d{1,2}\s?x\b|"
    r"karten\s?set|complete set|komplett|sleeve|binder|booster|pack\b|box\b|deck\b|tin\b|display|case\b|starter|"
    r"spanish|espa[nñ]ol|italian|italiano|french|fran[cç]ais|german|deutsch|portugu|korean|chinese|"
    r"spanisch|italienisch|franz[oö]sisch|portugiesisch|polnisch|koreanisch|chinesisch|russisch|"
    r"ita|spa|fra|kor|chn|ger|anglais|"
    r"\bde\b|\bfr\b|\bit\b|\bes\b|coin|m[uü]nze|sticker|topps|carddass|bandai|meiji|amada|ensky|"
    r"jumbo|oversize[d]?|poster|figur(?:e|ine)?|plush|badge|magnet|puzzle|folder|album|portfolio|"
    r"code\b|codes\b|tcgo|ptcgo|mystery(?!\s+of\s+the)|surprise|grab\s*bag|random|zufall|[uü]berraschung|empty|leer)\b",
    re.I,
)
# Karten ab 2017 (Sun & Moon und alles danach). Bewusst NICHT hier: ex/EX (gibt es 2003-2007
# und 2011-2016 als echte Karten), BREAK, Mega, Full Art, Secret Rare, LV.X, Prime, LEGEND.
MODERN = re.compile(
    r"\b(gx|vmax|vstar|v-?union|tag\s*team|swsh\d*|sm\d{1,3}|sv\d{1,2}[a-z]?|"
    r"sun\s*(?:&|and)?\s*moon|sonne\s*(?:&|und)?\s*mond|sword\s*(?:&|and)?\s*shield|schwert|"
    r"scarlet|violet|paldea|paldean|obsidian\s*flames|paradox\s*rift|temporal\s*forces|twilight\s*masquerade|"
    r"surging\s*sparks|stellar\s*crown|destined\s*rivals|journey\s*together|prismatic|crown\s*zenith|"
    r"evolving\s*skies|celebrations|shining\s*fates|shining\s*legends|hidden\s*fates|cosmic\s*eclipse|"
    r"unified\s*minds|unbroken\s*bonds|team\s*up|lost\s*thunder|dragon\s*majesty|celestial\s*storm|"
    r"forbidden\s*light|ultra\s*prism|crimson\s*invasion|burning\s*shadows|guardians\s*rising|"
    r"detective\s*pikachu|lost\s*origin|astral\s*radiance|brilliant\s*stars|fusion\s*strike|"
    r"battle\s*styles|chilling\s*reign|vivid\s*voltage|darkness\s*ablaze|rebel\s*clash|"
    r"\d{1,3}\s*/\s*(?:sm|sv|s|m)\s*-\s*p|svp\w*|mep\b|30th|25th|mega\s*evolution|phantasmal|ascended\s*heroes|black\s*bolt|white\s*flare|perfect\s*order|chaos\s*rising|"
    r"mega\s*brave|mega\s*symphonia|illustration\s*rare|special\s*illustration|alt(?:ernate)?\s*art|"
    r"trainer\s*gallery|pokemon\s*go|pgo|151|20(?:1[7-9]|2\d))\b|\bradiant\s+(?!collection)|\bv\b",
    re.I,
)
NONHOLO = re.compile(r"non[\s-]*holo|no[\s-]?holo|nicht[\s-]?holo|\bregular\b|\bcommon\b|uncommon|reverse", re.I)
# PSA-gegradete Karten gehoeren in den PSA-Bot (#psa-vintage-deals), nicht hierher. Auch ein
# nacktes "PSA" ohne Grade-Zahl im Titel/Zustandsfeld gilt als gegradet - ausser es steht
# ausdruecklich "no PSA", "PSA ready", "for PSA" o.ae. (Karte ist ungegradet).
PSA_GRADED = re.compile(r"\bpsa\s*-?\s*(10|[1-9])(?!\.?\d)\b", re.I)
PSA_WORD = re.compile(r"\bpsa\b", re.I)
PSA_NEG = re.compile(
    r"\b(?:no|not|ungraded|raw|for|ready|worthy|potential|candidate|gradeable|grade[- ]?able)\s*-?\s*psa\b|"
    r"\bpsa[- ]?(?:ready|worthy|candidate|potential|grading)\b", re.I)

GOLD_STAR_RE = re.compile(r"gold\s*star|☆|★")
COND_STRIP = re.compile(
    r"\b(nm|ex|lp|mp|hp|vg|gd|pl)\s*[-/+]\s*(nm|ex|lp|mp|hp|vg|gd|pl|mint)\b|\bcondition\b\s*:?\s*\w+|"
    r"\b(near\s*mint|mint)\s*[-/]\s*ex\b", re.I)
VARIANT_TAGS = [
    ("Secret Rare", re.compile(r"secret|rainbow|gold\s*(?:rare|card)")),
    ("Full Art", re.compile(r"full\s*art|\bfa\b")),
    ("Mega EX", re.compile(r"\bmega\b|\bm[\s\-]+\w+[\s\-]+ex\b")),
    ("EX", re.compile(r"\bex\b")),
    ("LV.X", re.compile(r"\blv\.?\s*x\b|\blevel\s*x\b")),
    ("Prime", re.compile(r"\bprime\b")),
    ("LEGEND", re.compile(r"\blegend\b")),
    ("BREAK", re.compile(r"\bbreak\b")),
    ("Shining", re.compile(r"\bshining\b|\bshiny\b")),
    ("Delta", re.compile(r"δ|\bdelta\b")),
]
HOLO_RE = re.compile(r"\bholo(?:foil|graphic)?\b")
FIRST_ED_RE = re.compile(r"1st\s*ed|first\s*edition|(?<![0-9a-z])1st(?![0-9a-z])|1\.?\s*edition|erstauflage|(?<![0-9a-z])1ed(?![0-9a-z])")


PROMO_CODE_RE = re.compile(r"\b(xy|bw|dp|hgss)\s*-?\s*(\d{2,3})(?!\s*/)\b")


def detect_set(t):
    """(Set-Name, Aera, Match-Objekt) oder (None, None, None)."""
    for name, rx, era in SETS_C:
        m = rx.search(t)
        if m:
            if name == "JP Promo":
                jm = re.search(r"(\d{1,3})\s*/\s*(xy|bw|dpt?|hgss|adv|pcg|l)\s*-?\s*p\b", t)
                name = f"JP Promo {jm.group(2).upper()}-P #{int(jm.group(1))}"
            elif name == "Black Star Promo":
                pc = PROMO_CODE_RE.search(t)
                if pc:
                    name = f"Promo {pc.group(1).upper()}{pc.group(2)}"
                else:
                    # WOTC-Promos (Mew #8/#9, Pikachu #1 ...): Nummer aus "#9" im Titel
                    pn = re.search(r"#\s*(\d{1,3})\b", t)
                    if pn:
                        name = f"Black Star Promo #{pn.group(1)}"
            return name, era, m
    return None, None, None


def detect_name(tn):
    """Findet den Pokemon-/Trainer-Namen (normalisierter Titel!). Gibt (Anzeigename, Basisname)
    zurueck. Praefixe wie Dark/Light/Shining/Rocket's/Blaine's gehoeren zum Karten-Namen."""
    m = NAME_RE.search(tn)
    if not m:
        return None, None
    base = re.sub(r"[\s\-.']+", " ", m.group(1)).strip()
    base_key = base.replace(" ", "-") if base in ("ho oh",) else base
    pm = PREFIX_RE.search(tn[:m.start()].rstrip())
    prefix = pm.group(1).strip() if pm else ""
    disp = string.capwords((prefix + " " + base).strip())
    return disp, base_key


def is_gold_star(tt, base):
    if GOLD_STAR_RE.search(tt):
        return True
    # EX-Aera-Goldstars heissen offiziell nur "<Name> Star" (z.B. "Rayquaza Star")
    return bool(base and re.search(re.escape(base) + r"\s*[-–]?\s*star\b(?!\s*(?:promo|wars))", tt))


def detect_variant(tt, base, era=None):
    """(Variante fuer den Vergleichsschluessel, Tag-Liste fuer die Anzeige)."""
    tags = []
    # Gold Stars gibt es nur in der EX-Aera (inkl. POP-Serie und japanischen ADV/PCG-Serien). In
    # allen anderen Aeren ist "Gold Star" im Titel Keyword-Spam ("... BGS No Shining Goldstar") -
    # und "No (Shining) Gold Star" heisst ausdruecklich: KEINE Gold Star.
    tt_ng = re.sub(r"\bno\s+(?:shining\s+)?gold\s*star\b", " ", tt)
    gold = era == "EX" and is_gold_star(tt_ng, base)
    if gold:
        tags.append("Gold Star")
    key_variant = "Gold Star" if gold else None
    for tag, rx in VARIANT_TAGS:
        # In der EX-Aera steckt "EX" auch im Set-Namen ("EX Dragon Frontiers") - ob eine Karte
        # selbst eine ex-Karte ist, laesst sich dort aus dem Titel nicht verlaesslich ableiten.
        if tag == "EX" and era == "EX":
            continue
        if rx.search(tt):
            tags.append(tag)
            if key_variant is None:
                key_variant = tag
    if HOLO_RE.search(tt):
        tags.append("Holo")
    first = bool(FIRST_ED_RE.search(tt))
    if first:
        tags.append("1st Edition")
    variant = (key_variant or "Std") + ("+1st" if first else "")
    return variant, tags


def detect_grade(t):
    m = re.search(r"\b(psa|bgs|cgc|sgc|csg|ags|beckett|ace|pca|gma|mnt)\s*-?\s*(10|9\.5|9|8\.5|8|7\.5|7|6\.5|6|5\.5|5|4|3|2|1)\b", t, re.I)
    if m:
        return f"{m.group(1).upper()} {m.group(2)}"
    # eBays offizielle "Artikelzustand"-Stufen fuer ungegradete Sammelkarten:
    # Near Mint or Better / Lightly Played (Excellent) / Moderately Played (Very Good) /
    # Heavily Played (Poor) / Damaged - DE: Neuwertig oder besser / Leicht bespielt (Ausgezeichnet) /
    # Mäßig bespielt (Sehr gut) / Stark bespielt (Schlecht) / Beschädigt.
    # WICHTIG: "(Very Good)"/"(Sehr gut)" gehoert bei eBay zur MITTLEREN Stufe (Moderately
    # Played), nicht zu Lightly Played - deshalb zuerst und getrennt von "Excellent" pruefen.
    if re.search(r"near\s*mint\s*or\s*better|neuwertig\s*oder\s*besser", t, re.I):
        return "raw NM (eBay: Near Mint or Better)"
    if re.search(r"moderately\s*played|m[aä]{1,2}s{1,2}ig\s*bespielt|very\s*good|sehr\s*gut", t, re.I):
        return "raw MP (eBay: Moderately Played/Very Good)"
    if re.search(r"lightly\s*played|leicht\s*bespielt|\bexcellent\b|ausgezeichnet", t, re.I):
        return "raw LP (eBay: Lightly Played/Excellent)"
    if re.search(r"heavily\s*played|stark\s*bespielt|\bpoor\b|\bschlecht\b", t, re.I):
        return "raw HP (eBay: Heavily Played/Poor)"
    if re.search(r"\bdamaged\b|beschädigt", t, re.I):
        return "raw Damaged"
    # Fallback: generischer Titel-Slang, falls kein offizielles eBay-Zustandsfeld gefunden wurde.
    # ACHTUNG: ein nacktes "EX" ist hier KEIN Zustand - es ist (ab EX-Aera) meist der Kartentyp
    # ("Rayquaza EX", "Charizard-EX") und hat bei den erweiterten Aeren massenhaft Karten faelschlich
    # als "Excellent" eingestuft. Zustand "EX" nur in Kombinationen wie "NM/EX", "EX-NM", "EXC".
    if re.search(r"\b(nm|mint|exc)\b|\b(?:nm|ex)\s*[-/+]\s*(?:nm|ex|mint)\b", t, re.I):
        return "raw NM/EX (Titel)"
    if re.search(r"\b(lp|pl|vg\+?)\b", t, re.I):
        return "raw LP (Titel)"
    # "mp"/"hp" NUR als eigenstaendiges Wort matchen (kein \w*-Suffix!), sonst matcht z.B. die
    # Pokemon-Statzeile "HP70"/"HP100" im Titel faelschlich als Zustand "HP" (Heavily Played).
    if re.search(r"\b(mp|hp)\b", t, re.I) or re.search(
        r"\b(moderat|played|heavy|heavily|damaged|dmg|poor|beschädigt|bespielt|bend|bent|"
        r"crease|creased|knick|geknickt|inked|ink|written|scratch|kratzer|whitening|riss|torn)\w*\b",
        t, re.I,
    ):
        return "raw MP/HP (Titel)"
    return "raw ?"


def grade_band(g):
    """Grobe Zustandsstufe fuer den Vergleichsschluessel (sonst wird jede Karte in 7 Zustands-
    Toepfe zerhackt und keiner erreicht genug Angebote). Gegradete Karten (BGS/CGC...) bleiben
    nach exaktem Grade getrennt."""
    if not g.startswith("raw"):
        return g
    if re.search(r"MP|HP|Damaged", g):
        return "raw worn"
    if re.search(r"NM|LP", g):
        return "raw good"
    return "raw ?"


def is_japanese(t):
    return bool(re.search(r"japan|japanese|japanisch|\bjp\b|\bjpn\b|old back|no rarity|vending|carddass|\bpcg\b|\badv\b", t, re.I))


def parse_chf(s):
    if not s:
        return None
    m = re.search(r"CHF\s*([\d'.]+),(\d{2})", s)
    if not m:
        m2 = re.search(r"CHF\s*([\d'.]+)", s)
        return float(m2.group(1).replace("'", "").replace(".", "")) if m2 else None
    return float(m.group(1).replace("'", "").replace(".", "") + "." + m.group(2))


def parse_shipping(s):
    if not s:
        return 0.0
    if re.search(r"kostenlos|gratis|free", s, re.I):
        return 0.0
    v = parse_chf(s)
    return v if v is not None else 0.0


def log(msg):
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line)
    with open(LOG_FILE, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


# ------------------------------------------------------- Haendler-Herkunft
# Kein Laender-Ausschluss (zu hohe Versandkosten werden ueber MIN_TOTAL/MAX_TOTAL - Preis +
# Versand - abgefangen). Japan bleibt Top-Praeferenz und wird nach vorne gezogen und markiert.
def is_japan_origin(country_raw):
    return (country_raw or "").strip().lower() == "japan"


EXTRACT_JS = """() => [...document.querySelectorAll('li.s-item, li.s-card')].map(li => {
  const a = li.querySelector('a[href*="/itm/"]');
  const id = a ? (a.href.match(/itm[/](\\d+)/) || [])[1] : '';
  const t = (li.querySelector('.s-item__title, .s-card__title') || {}).textContent || '';
  const p = (li.querySelector('.s-item__price, .s-card__price') || {}).textContent || '';
  const s = (li.querySelector('.s-item__shipping, .s-item__logisticsCost, .s-card__shipping, [class*="shipping"], [class*="logistics"]') || {}).textContent || '';
  const img = li.querySelector('img');
  const sub = [...li.querySelectorAll('.SECONDARY_INFO, .s-item__subtitle, .s-card__subtitle, [class*="condition"], [class*="Condition"]')].map(x => x.textContent).join(' ');
  const full = (li.innerText || '').replace(/\\s+/g, ' ').trim();
  const locEl = (li.querySelector('.s-item__location, .s-item__itemLocation, [class*="location"]') || {}).textContent || '';
  const fromMatch = (li.innerText || '').match(/\\baus ([^\\n]+)/);
  const country = (locEl.replace(/^aus\\s+/i, '').trim()) || (fromMatch ? fromMatch[1].trim() : '');
  return {id, t: t.replace('Wird in neuem Fenster oder Tab geöffnet', '').trim(), p: p.trim(), s: s.trim(),
          img: img ? (img.src || img.getAttribute('data-src') || '') : '', sub: sub.trim(), full, country};
}).filter(x => x.id && x.id !== '123456')"""


def fetch_all():
    items = {}
    # eBay blockt echten Headless-Chrome (403/Stub-Seite, getestet 2026-09-26: urllib, curl,
    # Playwright headless=True, Chrome "--headless=new" und sogar camoufox headless). Nur ein
    # "echter" (headed) Chromium-Prozess funktioniert. Lokal (Windows) wird das Fenster
    # ausserhalb des Desktops platziert, in der Cloud (Linux) laeuft es unter xvfb-run.
    launch_args = ["--window-size=1280,900"]
    if os.name == "nt":
        launch_args.insert(0, "--window-position=-2400,-2400")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=launch_args)
        ctx = browser.new_context(locale="de-CH", viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        for q in SEARCHES:
            hi = (MAX_TOTAL_GOLD if "gold star" in q else MAX_TOTAL) + 60
            url = ("https://www.ebay.ch/sch/i.html?_nkw=" + urllib.parse.quote(q) +
                   f"&_sacat=183454&LH_BIN=1&_sop=10&_udlo={int(MIN_TOTAL)}&_udhi={int(hi)}&_ipg=240")
            rows = []
            for attempt in range(3):
                try:
                    page.goto(url, timeout=60000, wait_until="domcontentloaded")
                    page.wait_for_timeout(PAGE_WAIT_MS * (attempt + 1))
                    rows = page.evaluate(EXTRACT_JS)
                except Exception as e:
                    log(f"Fehler bei '{q}' (Versuch {attempt + 1}): {e}")
                if rows:
                    break
                time.sleep(3)
            new = 0
            for r in rows:
                if r["id"] not in items:
                    r["query"] = q
                    items[r["id"]] = r
                    new += 1
            log(f"'{q}': {len(rows)} Treffer, {new} neu")
            time.sleep(1.0)
        browser.close()
    return items


DROP_STATS = {}      # Diagnose: warum Angebote verworfen wurden (Grund -> Anzahl)
GOLD_DROPPED = []    # Diagnose: verworfene Titel, die nach Gold Star aussehen (max. 40)


def _drop(reason, title):
    DROP_STATS[reason] = DROP_STATS.get(reason, 0) + 1
    if len(GOLD_DROPPED) < 40 and re.search(r"gold\s*star|☆|★|\bstar\b", title, re.I):
        GOLD_DROPPED.append(f"[{reason}] {title[:90]}")


def classify(items):
    out = []
    for r in items.values():
        t = r["t"]
        full_text = " ".join([t, r.get("sub", ""), r.get("full", "")])
        if EXCLUDE.search(t):
            _drop("ausschluss-wort", t)
            continue
        if NONHOLO.search(t):
            _drop("non-holo/reverse", t)
            continue
        if MODERN.search(t):
            _drop("modern(ab 2017)", t)
            continue
        if PSA_GRADED.search(full_text) or (PSA_WORD.search(full_text) and not PSA_NEG.search(full_text)):
            _drop("psa(eigener bot)", t)
            continue
        price = parse_chf(r["p"])
        if price is None:
            _drop("kein-preis", t)
            continue
        is_jp_seller = is_japan_origin(r.get("country", ""))
        ship = parse_shipping(r["s"])
        # Fehlt der Versandpreis in der Trefferliste (v.a. Auslandsverkaeufer mit "eBay International
        # Shipping"), wuerde der Gesamtpreis zu niedrig ausfallen (Fehlalarm 2026-10-06) -> Pauschale.
        ship_est = not re.search(r"CHF|kostenlos|gratis|free", r["s"] or "", re.I)
        if ship_est:
            ship = 8.0 if (r.get("country") or "").strip().lower() == "schweiz" else 25.0
        total = round(price + ship, 2)
        sset, era, sm = detect_set(norm(t))
        if not sset:
            _drop("kein-set", t)
            continue
        # Set-Namen aus dem Titel entfernen, bevor Name/Variante gesucht werden (sonst steckt
        # z.B. "Dragon" in "EX Dragon Frontiers" oder "Black"/"White" im Set-Namen in den Tags).
        tn = norm(t)
        tn_wo_set = (tn[:sm.start()] + " " + tn[sm.end():]) if sm else tn
        disp_name, base = detect_name(tn_wo_set)
        if not disp_name:
            disp_name, base = detect_name(tn)
        if not disp_name:
            _drop("kein-name", t)
            continue
        tt = COND_STRIP.sub(" ", tn_wo_set)
        variant, tags = detect_variant(tt, base, era)
        # Zustand steht meistens NICHT im Titel, sondern im "Artikelzustand"-Feld der Trefferkarte
        grade = detect_grade(" ".join([t, r.get("sub", ""), r.get("full", "")]))
        lang = "JP" if is_japanese(t) else "EN"
        key = f"{lang}|{sset}|{disp_name}|{variant}|{grade_band(grade)}"
        img = r["img"] or ""
        img = re.sub(r"/s-l\d+\.(webp|jpg)", "/s-l1600.jpg", img)
        gold = "Gold Star" in tags
        popular = base in POPULAR
        rare = variant != "Std" or "Holo" in tags
        if gold:
            tier = 0
        elif popular and rare:
            tier = 1
        elif popular or rare:
            tier = 2
        else:
            tier = 3
        out.append(dict(id=r["id"], title=t, price=price, ship=ship, ship_est=ship_est, total=total, name=disp_name,
                        set=sset, era=era, grade=grade, lang=lang, variant=variant, tags=tags, key=key, tier=tier,
                        img=img, url=f"https://www.ebay.ch/itm/{r['id']}", sub=r.get("sub", ""),
                        query=r.get("query", ""), country=r.get("country", "") or "unbekannt", is_jp=is_jp_seller))
    return out


def reference_price(vals, min_samples=None):
    """Robuster Referenzpreis: Median, nach Entfernen von Ausreissern (>2.5x oder <0.4x des
    Roh-Medians) nochmals Median. None, wenn danach zu wenig Angebote uebrig sind."""
    ms = min_samples or MIN_SAMPLES
    if len(vals) < ms:
        return None, 0
    raw = statistics.median(vals)
    kept = [v for v in vals if 0.4 * raw <= v <= 2.5 * raw]
    if len(kept) < ms:
        return None, len(kept)
    return statistics.median(kept), len(kept)


def find_deals(cards, history):
    # Pro Karte ein Pool {Angebots-ID: Gesamtpreis}: Historie frueherer Laeufe + aktuelle
    # Live-Angebote (live ueberschreibt Historie derselben ID -> kein Doppelzaehlen).
    pool = {}
    for key, bucket in history.items():
        pool[key] = {lid: v[0] for lid, v in bucket.items()}
    for c in cards:
        pool.setdefault(c["key"], {})[c["id"]] = c["total"]
    deals = []
    for c in cards:
        if not (MIN_TOTAL <= c["total"] <= (MAX_TOTAL_GOLD if c["tier"] == 0 else MAX_TOTAL)):
            continue
        # Referenz OHNE das Angebot selbst (sonst zieht der eigene Preis den Median nach unten)
        others = [t for lid, t in pool[c["key"]].items() if lid != c["id"]]
        med, n = reference_price(others, MIN_SAMPLES_GOLD if c["tier"] == 0 else MIN_SAMPLES)
        if med is None or med < MIN_MEDIAN:
            continue
        if c["total"] <= (DEAL_RATIO if n >= 8 else DEAL_RATIO_THIN) * med:
            deals.append(dict(c, median=round(med, 2), n=n, ratio=round(c["total"] / med, 2)))
    # Gold Stars zuerst, dann beliebte seltene Karten, dann der Rest; innerhalb einer Stufe
    # japanische Verkaeufer zuerst, danach groesster Abstand zum Median.
    deals.sort(key=lambda d: (d["tier"], 0 if d["is_jp"] else 1, d["ratio"]))
    return deals


def update_history(history, cards):
    """Traegt die aktuell klassifizierten Angebote in die Preishistorie ein (pro Karte,
    dedupliziert nach Angebots-ID, kompaktes Format [Preis, JJMMTT]), entfernt Eintraege, die
    seit HISTORY_MAX_DAYS nicht mehr gesehen wurden (vermutlich verkauft/entfernt) und kappt
    jede Karte auf HISTORY_MAX_PER_KEY (neueste zuerst)."""
    today = datetime.now().strftime("%y%m%d")
    for c in cards:
        if not (MIN_TOTAL <= c["total"] <= (MAX_TOTAL_GOLD * 2 if c["tier"] == 0 else MAX_TOTAL * 2)):
            continue
        history.setdefault(c["key"], {})[c["id"]] = [c["total"], today]
    cutoff = (datetime.now().timestamp() - HISTORY_MAX_DAYS * 86400)
    for key in list(history.keys()):
        bucket = history[key]
        for lid in list(bucket.keys()):
            try:
                ts = datetime.strptime(bucket[lid][1], "%y%m%d").timestamp()
            except Exception:
                ts = 0
            if ts < cutoff:
                del bucket[lid]
        if len(bucket) > HISTORY_MAX_PER_KEY:
            keep = sorted(bucket.items(), key=lambda kv: kv[1][1], reverse=True)[:HISTORY_MAX_PER_KEY]
            history[key] = dict(keep)
        if not history[key]:
            del history[key]


def load_state():
    if RESET or not os.path.exists(STATE_FILE):
        return {"seen": {}, "history": {}}
    try:
        st = json.load(open(STATE_FILE, encoding="utf-8"))
        st.setdefault("seen", {})
        st.setdefault("history", {})
        return st
    except Exception:
        return {"seen": {}, "history": {}}


def save_state(st):
    json.dump(st, open(STATE_FILE, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))


def post_discord(webhook, deals):
    embeds = []
    for d in deals:
        pct = int(round((1 - d["ratio"]) * 100))
        flag = "\U0001F1EF\U0001F1F5 " if d.get("is_jp") else ""
        star = "⭐ " if "Gold Star" in d["tags"] else ""
        tags = ", ".join(d["tags"]) if d["tags"] else "-"
        desc = (f"**CHF {d['total']:.2f}** inkl. Versand (Preis {d['price']:.2f} + Versand {d['ship']:.2f}{' GESCHAETZT' if d.get('ship_est') else ''})\n"
                f"Karte: **{d['name']}** - {d['set']} ({d['lang']}, Aera {d['era']}) - Zustand/Grade: **{d['grade']}**\n"
                f"Merkmale: {tags}\n"
                f"Herkunft: **{d.get('country', 'unbekannt')}**\n"
                f"**{pct} % unter Median** (Median CHF {d['median']:.2f} aus {d['n']} anderen Angeboten inkl. Preishistorie)\n"
                f"[Zum Angebot]({d['url']})")
        color = 0xFFD700 if "Gold Star" in d["tags"] else (0x2ECC71 if pct >= 40 else 0xF1C40F)
        embeds.append({
            "title": (flag + star + d["title"])[:240],
            "url": d["url"],
            "description": desc,
            "color": color,
            "image": {"url": d["img"]} if d["img"] else None,
            "footer": {"text": f"eBay.ch Sofort-Kaufen - gefunden {datetime.now():%d.%m.%Y %H:%M} - Suche: {d.get('query', '')}"[:2000]},
        })
    for e in embeds:
        if e["image"] is None:
            del e["image"]
    for i in range(0, len(embeds), 6):
        payload = {"username": "CHAU Vintage-Deal-Finder", "embeds": embeds[i:i + 6]}
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(webhook, data=data, headers={"Content-Type": "application/json", "User-Agent": "chau-deal-finder/2.0"})
        with urllib.request.urlopen(req) as r:
            log(f"Discord {r.status} ({len(embeds[i:i + 6])} Embeds)")
        time.sleep(1.5)


def acquire_lock():
    if os.path.exists(LOCK_FILE):
        if time.time() - os.path.getmtime(LOCK_FILE) < 900:
            return False
    open(LOCK_FILE, "w").write(str(os.getpid()))
    return True


def main():
    if not acquire_lock():
        log("Ein anderer Lauf ist bereits aktiv, abgebrochen.")
        return
    try:
        webhook = os.environ.get("DISCORD_WEBHOOK_HOLO") or ""
        if not webhook and not DRY_RUN and os.name == "nt":
            # Lokal (Windows) steht der Webhook als persistente User-Umgebungsvariable; in der
            # Cloud (Linux) kommt er ausschliesslich ueber os.environ (GitHub Actions Secret).
            import subprocess
            webhook = subprocess.run(["powershell", "-NoProfile", "-Command", "[Environment]::GetEnvironmentVariable('DISCORD_WEBHOOK_HOLO','User')"],
                                     capture_output=True, text=True).stdout.strip()
        if not webhook and not DRY_RUN:
            log("Webhook DISCORD_WEBHOOK_HOLO fehlt.")
            return
        st = load_state()
        items = fetch_all()
        cards = classify(items)
        # find_deals() nutzt die BISHERIGE Historie (Stand vor diesem Lauf) - erst danach wird der
        # aktuelle Lauf in die Historie eingetragen.
        deals = find_deals(cards, st["history"])
        log(f"{len(items)} Angebote geladen, {len(cards)} klassifiziert, {len(deals)} Deals gesamt")
        by_era = {}
        for c in cards:
            by_era[c["era"]] = by_era.get(c["era"], 0) + 1
        log("Klassifiziert pro Aera: " + ", ".join(f"{k}={v}" for k, v in sorted(by_era.items())))
        by_tier = {}
        for c in cards:
            by_tier[c["tier"]] = by_tier.get(c["tier"], 0) + 1
        log("Klassifiziert pro Stufe (0=Gold Star, 1=beliebt+selten, 2=eins davon, 3=Rest): "
            + ", ".join(f"{k}={v}" for k, v in sorted(by_tier.items())))
        log("Verworfen: " + ", ".join(f"{k}={v}" for k, v in sorted(DROP_STATS.items())))
        for gd in GOLD_DROPPED[:25]:
            log("  verworfen (Gold-Star-verdaechtig) " + gd)
        gold_cards = [c for c in cards if c["tier"] == 0]
        log(f"Gold-Star-Karten klassifiziert: {len(gold_cards)}")
        for c in sorted(gold_cards, key=lambda x: x["total"])[:12]:
            log(f"  GOLD {c['set']} | {c['name']} | CHF {c['total']:.2f} | {c['title'][:70]}")
        # Pro Karte hoechstens 2 Meldungen pro Lauf, sonst fuellt eine einzelne Karte (z.B. mehrere
        # Rayquaza-EX-Promos) die ganze Liste - so verteilen sich die Meldungen ueber mehr Karten.
        new, per_key = [], {}
        for d in deals:
            if d["id"] in st["seen"] or per_key.get(d["key"], 0) >= 2:
                continue
            per_key[d["key"]] = per_key.get(d["key"], 0) + 1
            new.append(d)
            if len(new) >= MAX_POSTS_PER_RUN:
                break
        for d in deals[:30]:
            log(f"  {'NEU ' if d['id'] not in st['seen'] else '    '}T{d['tier']} {d['ratio']:.2f}x CHF {d['total']:.2f} (Median {d['median']:.2f}, n={d['n']}) {d['key']} | {d['title'][:70]} | {d['url']}")
        if DRY_RUN:
            log(f"Dry-Run: {len(new)} neue Deals wuerden gepostet.")
            return
        if new:
            post_discord(webhook, new)
        update_history(st["history"], cards)
        for d in new:
            st["seen"][d["id"]] = datetime.now().strftime("%Y-%m-%d")
        # alte Eintraege nach 60 Tagen vergessen
        cutoff = datetime.now().timestamp() - 60 * 86400
        st["seen"] = {k: v for k, v in st["seen"].items() if datetime.strptime(v, "%Y-%m-%d").timestamp() > cutoff}
        save_state(st)
        log(f"{len(new)} neue Deals gepostet.")
    finally:
        try:
            os.remove(LOCK_FILE)
        except OSError:
            pass


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
