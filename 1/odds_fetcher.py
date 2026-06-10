"""
Odds Fetcher — the-odds-api.com
================================
Hämtar automatiskt odds för alla VM-matcher.
Körs av update.py inför varje omgång.

Gratis tier: 500 requests/månad
VM 2026: 5 omgångar × 13 matcher = 65 requests totalt
"""

import requests
import json
import os
from pathlib import Path
from datetime import datetime, timezone

# API-nyckel från miljövariabel (GitHub Secret)
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
BASE_URL = "https://api.the-odds-api.com/v4"

# VM 2026 sport-nyckel
SPORT = "soccer_fifa_world_cup_2026"

# Bookmakers att hämta odds från (prioritetsordning)
BOOKMAKERS = "unibet,betsson,pinnacle,bet365,williamhill"

# Svenska lag-namnsmappning
TEAM_NAME_SV = {
    "Mexico":           "Mexiko",
    "South Africa":     "Sydafrika",
    "South Korea":      "Sydkorea",
    "Czech Republic":   "Tjeckien",
    "Canada":           "Kanada",
    "Bosnia and Herzegovina": "Bosnien",
    "United States":    "USA",
    "Brazil":           "Brasilien",
    "Morocco":          "Marocko",
    "Australia":        "Australien",
    "Turkey":           "Turkiet",
    "Germany":          "Tyskland",
    "Curacao":          "Curacao",
    "Netherlands":      "Nederländerna",
    "Japan":            "Japan",
    "Ivory Coast":      "Elfenbenskusten",
    "Ecuador":          "Ecuador",
    "Belgium":          "Belgien",
    "Egypt":            "Egypten",
    "France":           "Frankrike",
    "Senegal":          "Senegal",
    "Iraq":             "Irak",
    "Norway":           "Norge",
    "Argentina":        "Argentina",
    "Algeria":          "Algeriet",
    "England":          "England",
    "Spain":            "Spanien",
    "Portugal":         "Portugal",
    "Croatia":          "Kroatien",
    "Switzerland":      "Schweiz",
    "Denmark":          "Danmark",
    "Poland":           "Polen",
    "Uruguay":          "Uruguay",
    "Colombia":         "Colombia",
    "Chile":            "Chile",
    "Venezuela":        "Venezuela",
    "Peru":             "Peru",
    "Bolivia":          "Bolivia",
    "Iran":             "Iran",
    "Saudi Arabia":     "Saudiarabien",
    "Qatar":            "Qatar",
    "New Zealand":      "Nya Zeeland",
    "Tunisia":          "Tunisien",
    "Cameroon":         "Kamerun",
    "Ghana":            "Ghana",
    "Senegal":          "Senegal",
    "Nigeria":          "Nigeria",
    "Scotland":         "Skottland",
    "Haiti":            "Haiti",
    "Panama":           "Panama",
    "Costa Rica":       "Costa Rica",
    "Honduras":         "Honduras",
    "Jamaica":          "Jamaica",
}


def get_available_sports():
    """Listar alla tillgängliga sporter för att hitta rätt VM-nyckel"""
    url = f"{BASE_URL}/sports"
    params = {"apiKey": ODDS_API_KEY}
    
    r = requests.get(url, params=params, timeout=15)
    if r.status_code == 200:
        sports = r.json()
        wc = [s for s in sports if "world_cup" in s.get("key", "").lower() or 
              "fifa" in s.get("key", "").lower()]
        return wc
    return []


def get_wc_odds():
    """
    Hämtar alla VM-matcher med odds från the-odds-api.com
    Returnerar lista med matcher och deras odds
    """
    if not ODDS_API_KEY:
        print("  ⚠️  ODDS_API_KEY saknas — hoppar över odds-hämtning")
        return []
    
    print(f"  📡 Hämtar odds från the-odds-api.com...")
    
    # Prova olika sport-nycklar för VM 2026
    sport_keys = [
        "soccer_fifa_world_cup_2026",
        "soccer_world_cup_winner",
        "soccer_fifa_world_cup",
    ]
    
    # Hitta rätt nyckel
    available = get_available_sports()
    if available:
        sport_keys = [s["key"] for s in available] + sport_keys
    
    for sport_key in sport_keys:
        url = f"{BASE_URL}/sports/{sport_key}/odds"
        params = {
            "apiKey":    ODDS_API_KEY,
            "regions":   "eu",
            "markets":   "h2h",
            "bookmakers": BOOKMAKERS,
            "oddsFormat": "decimal",
        }
        
        r = requests.get(url, params=params, timeout=15)
        
        # Logga requests kvar
        remaining = r.headers.get("x-requests-remaining", "?")
        used = r.headers.get("x-requests-used", "?")
        
        if r.status_code == 200:
            data = r.json()
            if data:
                print(f"  ✅ Odds hämtade via {sport_key}")
                print(f"     Requests: {used} använda, {remaining} kvar denna månad")
                return parse_odds(data)
        elif r.status_code == 404:
            continue  # Prova nästa nyckel
        elif r.status_code == 401:
            print(f"  ❌ Ogiltig API-nyckel")
            return []
        elif r.status_code == 429:
            print(f"  ❌ Rate limit nådd")
            return []
    
    print(f"  ⚠️  VM 2026 inte tillgängligt ännu på odds-API:t")
    print(f"     Använder manual odds från matches_config.json")
    return []


def parse_odds(games):
    """
    Parsear odds-data från the-odds-api.com
    Väljer bästa odds från tillgängliga bookmakers
    """
    result = []
    
    for game in games:
        home = game.get("home_team", "")
        away = game.get("away_team", "")
        commence = game.get("commence_time", "")
        
        # Samla odds från alla bookmakers
        best_h = 0
        best_d = 0
        best_a = 0
        bookmaker_used = ""
        
        for bm in game.get("bookmakers", []):
            for market in bm.get("markets", []):
                if market.get("key") != "h2h":
                    continue
                
                outcomes = {o["name"]: o["price"] for o in market.get("outcomes", [])}
                
                h = outcomes.get(home, 0)
                a = outcomes.get(away, 0)
                d = outcomes.get("Draw", 0)
                
                # Ta bästa odds (highest = most value)
                if h > best_h:
                    best_h = h
                    bookmaker_used = bm.get("title", "")
                if d > best_d:
                    best_d = d
                if a > best_a:
                    best_a = a
        
        if best_h > 0:
            home_sv = TEAM_NAME_SV.get(home, home)
            away_sv = TEAM_NAME_SV.get(away, away)
            
            result.append({
                "home":     home,
                "away":     away,
                "home_sv":  home_sv,
                "away_sv":  away_sv,
                "odds_h":   round(best_h, 2),
                "odds_d":   round(best_d, 2),
                "odds_a":   round(best_a, 2),
                "date":     commence,
                "bookmaker": bookmaker_used,
            })
    
    print(f"  ✅ {len(result)} matcher med odds parsade")
    return result


def get_streckning():
    """
    Hämtar Svenska Spels streckning för VM-tipset
    Används för att identifiera understrekade utfall
    """
    print("  📊 Hämtar streckning från Svenska Spel...")
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "sv-SE,sv;q=0.9,en;q=0.8",
        "Referer": "https://spela.svenskaspel.se/",
        "Origin": "https://spela.svenskaspel.se",
    }
    
    # Prova olika endpoints
    endpoints = [
        "https://spela.svenskaspel.se/api/coupon/vmtipset/draws",
        "https://www.svenskaspel.se/api/game/draws?gameTypes=VMTIPSET",
        "https://spela.svenskaspel.se/vmtipset",
    ]
    
    for url in endpoints:
        try:
            r = requests.get(url, headers=headers, timeout=10)
            if r.status_code == 200:
                ct = r.headers.get("content-type", "")
                if "json" in ct:
                    data = r.json()
                    streckning = extract_streckning(data)
                    if streckning:
                        print(f"  ✅ Streckning hämtad: {len(streckning)} matcher")
                        return streckning
        except Exception as e:
            continue
    
    print("  ⚠️  Streckning ej tillgänglig — uppdatera manuellt")
    return {}


def extract_streckning(data):
    """Extraherar streckning-procent från Svenska Spels JSON"""
    streckning = {}
    
    # Navigera JSON-strukturen
    draws = data.get("draws", data.get("data", []))
    if isinstance(draws, dict):
        draws = [draws]
    
    for draw in draws:
        events = draw.get("drawEvents", draw.get("events", []))
        for event in events:
            match = (event.get("eventDescription") or 
                    event.get("description") or 
                    event.get("name", ""))
            
            dist = (event.get("distribution") or 
                   event.get("odds") or 
                   event.get("percentage", {}))
            
            if match and dist:
                streckning[match] = {
                    "hemma": dist.get("home", dist.get("1", "?")),
                    "kryss":  dist.get("draw", dist.get("X", "?")),
                    "borta":  dist.get("away", dist.get("2", "?"))
                }
    
    return streckning


def merge_with_config(odds_data, config_path):
    """
    Mergar ny odds-data med befintlig matches_config.json
    Uppdaterar bara odds, behåller lag-namn och nummer
    """
    if not Path(config_path).exists():
        return odds_data
    
    with open(config_path) as f:
        config = json.load(f)
    
    if not odds_data:
        print("  ℹ️  Inga nya odds — behåller matches_config.json")
        return config
    
    # Matcha på lagnamn
    updated = 0
    for cfg_match in config:
        for odds_match in odds_data:
            home_match = (cfg_match["home"].lower() in odds_match["home"].lower() or
                         odds_match["home"].lower() in cfg_match["home"].lower())
            away_match = (cfg_match["away"].lower() in odds_match["away"].lower() or
                         odds_match["away"].lower() in cfg_match["away"].lower())
            
            if home_match and away_match:
                cfg_match["odds_h"] = odds_match["odds_h"]
                cfg_match["odds_d"] = odds_match["odds_d"]
                cfg_match["odds_a"] = odds_match["odds_a"]
                cfg_match["bookmaker"] = odds_match.get("bookmaker", "")
                updated += 1
                break
    
    print(f"  ✅ {updated}/{len(config)} matcher uppdaterade med nya odds")
    return config


if __name__ == "__main__":
    print("╔══════════════════════════════════════════════╗")
    print("║     ODDS FETCHER — TEST                      ║")
    print("╚══════════════════════════════════════════════╝")
    print()
    
    # Testa API
    sports = get_available_sports()
    print(f"Tillgängliga VM-sporter: {len(sports)}")
    for s in sports[:5]:
        print(f"  {s.get('key')} — {s.get('title')}")
    
    print()
    
    # Hämta odds
    odds = get_wc_odds()
    if odds:
        print(f"\nExempel odds:")
        for m in odds[:3]:
            print(f"  {m['home']} vs {m['away']}: H{m['odds_h']} X{m['odds_d']} A{m['odds_a']}")
    
    # Hämta streckning
    print()
    streckning = get_streckning()
    if streckning:
        for match, s in list(streckning.items())[:3]:
            print(f"  {match}: H{s['hemma']}% X{s['kryss']}% B{s['borta']}%")
