"""
xG Data Pipeline — Statsbomb Open Data
=======================================
Hämtar xG (expected goals) per match och lag från
Statsbombs gratis GitHub-dataset.

Täcker: VM 2018, VM 2022
Kan enkelt utökas till EM, WC 2026 när data släpps.

Kör: python3 xg_pipeline.py
"""

import requests
import json
import time
import pandas as pd
import numpy as np
from pathlib import Path

# ════════════════════════════════════════════
# CONFIG
# ════════════════════════════════════════════

BASE_URL = "https://raw.githubusercontent.com/statsbomb/open-data/master/data"
CACHE_DIR = Path("xg_cache")          # Sparar data lokalt så du inte hämtar om
CACHE_DIR.mkdir(exist_ok=True)

# Tillgängliga VM-tävlingar i Statsbomb open data
COMPETITIONS = {
    "WC_2022": {"competition_id": 43, "season_id": 106},
    "WC_2018": {"competition_id": 43, "season_id": 3},
}

# Lag-mapping: svenska namn → Statsbomb engelska namn
# Lägg till fler när VM 2026 börjar
TEAM_NAME_MAP = {
    # VM 2022 lag
    "Mexiko":          "Mexico",
    "Sydkorea":        "South Korea",
    "Kanada":          "Canada",
    "Sydafrika":       "South Africa",
    "Tjeckien":        "Czech Republic",
    "Bosnien":         "Bosnia and Herzegovina",
    "Frankrike":       "France",
    "Argentina":       "Argentina",
    "Brasilien":       "Brazil",
    "England":         "England",
    "Spanien":         "Spain",
    "Portugal":        "Portugal",
    "Kroatien":        "Croatia",
    "Marocko":         "Morocco",
    "Nederländerna":   "Netherlands",
    "USA":             "United States",
    "Japan":           "Japan",
    "Senegal":         "Senegal",
    "Australien":      "Australia",
    "Uruguay":         "Uruguay",
    "Polen":           "Poland",
    "Schweiz":         "Switzerland",
    "Danmark":         "Denmark",
    "Belgien":         "Belgium",
    "Ghana":           "Ghana",
    "Kamerun":         "Cameroon",
    "Qatar":           "Qatar",
    "Serbien":         "Serbia",
    "Wales":           "Wales",
    "Ecuador":         "Ecuador",
    "Tunisien":        "Tunisia",
    "Iran":            "Iran",
    "Saudiarabien":    "Saudi Arabia",
    "Costa Rica":      "Costa Rica",
    "Tyskland":        "Germany",
}


# ════════════════════════════════════════════
# STEG 1: DATA-HÄMTNING MED CACHING
# ════════════════════════════════════════════

def fetch_json(url, cache_file=None):
    """
    Hämtar JSON från URL.
    Cachar lokalt så du inte gör onödiga requests.
    """
    if cache_file and cache_file.exists():
        with open(cache_file) as f:
            return json.load(f)
    
    time.sleep(0.5)  # Respektera GitHub rate limits
    headers = {"User-Agent": "xG-Pipeline/1.0"}
    
    r = requests.get(url, headers=headers, timeout=30)
    
    if r.status_code != 200:
        print(f"  ⚠️  HTTP {r.status_code}: {url}")
        return None
    
    data = r.json()
    
    if cache_file:
        with open(cache_file, 'w') as f:
            json.dump(data, f)
    
    return data


def get_matches(competition_key):
    """
    Hämtar alla matcher för en tävling.
    Returnerar lista med match-dicts.
    """
    comp = COMPETITIONS[competition_key]
    cid, sid = comp["competition_id"], comp["season_id"]
    
    url = f"{BASE_URL}/matches/{cid}/{sid}.json"
    cache = CACHE_DIR / f"matches_{competition_key}.json"
    
    data = fetch_json(url, cache)
    if not data:
        return []
    
    print(f"  ✅ {competition_key}: {len(data)} matcher laddade")
    return data


def get_match_events(match_id):
    """
    Hämtar alla events (skott, passningar etc) för en match.
    Filtrerar direkt till skott för att spara minne.
    """
    url = f"{BASE_URL}/events/{match_id}.json"
    cache = CACHE_DIR / f"events_{match_id}.json"
    
    # Om cachad: läs och filtrera
    if cache.exists():
        with open(cache) as f:
            events = json.load(f)
        return [e for e in events if e.get("type", {}).get("name") == "Shot"]
    
    events = fetch_json(url)
    if not events:
        return []
    
    # Casha hela filen
    with open(cache, 'w') as f:
        json.dump(events, f)
    
    shots = [e for e in events if e.get("type", {}).get("name") == "Shot"]
    return shots


# ════════════════════════════════════════════
# STEG 2: xG-EXTRAKTION
# ════════════════════════════════════════════

def extract_xg_from_match(match_meta, shots):
    """
    Räknar ut xG per lag från skott-events.
    
    Returnerar dict med:
    - home_xg, away_xg: total xG
    - home_shots, away_shots: antal skott
    - home_xg_per_shot, away_xg_per_shot: effektivitet
    - shot_details: lista med varje skott
    """
    home_team = match_meta["home_team"]["home_team_name"]
    away_team = match_meta["away_team"]["away_team_name"]
    
    home_shots_data = []
    away_shots_data = []
    
    for shot in shots:
        team = shot.get("team", {}).get("name", "")
        player = shot.get("player", {}).get("name", "")
        shot_info = shot.get("shot", {})
        
        xg = shot_info.get("statsbomb_xg", 0) or 0
        outcome = shot_info.get("outcome", {}).get("name", "Unknown")
        technique = shot_info.get("technique", {}).get("name", "Normal")
        body_part = shot_info.get("body_part", {}).get("name", "Foot")
        
        # Koordinater
        location = shot.get("location", [0, 0])
        x, y = location[0], location[1]
        
        shot_record = {
            "player": player,
            "xg": xg,
            "outcome": outcome,
            "technique": technique,
            "body_part": body_part,
            "x": x,
            "y": y,
            "is_goal": outcome == "Goal"
        }
        
        if team == home_team:
            home_shots_data.append(shot_record)
        elif team == away_team:
            away_shots_data.append(shot_record)
    
    home_xg = sum(s["xg"] for s in home_shots_data)
    away_xg = sum(s["xg"] for s in away_shots_data)
    
    return {
        "match_id":          match_meta["match_id"],
        "date":              match_meta["match_date"],
        "home_team":         home_team,
        "away_team":         away_team,
        "home_score":        match_meta["home_score"],
        "away_score":        match_meta["away_score"],
        "home_xg":           round(home_xg, 4),
        "away_xg":           round(away_xg, 4),
        "home_shots":        len(home_shots_data),
        "away_shots":        len(away_shots_data),
        "home_xg_per_shot":  round(home_xg / max(len(home_shots_data), 1), 4),
        "away_xg_per_shot":  round(away_xg / max(len(away_shots_data), 1), 4),
        "xg_diff":           round(home_xg - away_xg, 4),
        "home_shots_detail": home_shots_data,
        "away_shots_detail": away_shots_data,
    }


# ════════════════════════════════════════════
# STEG 3: TEAM-AGGREGERING
# ════════════════════════════════════════════

def build_team_xg_profile(match_xg_list):
    """
    Aggregerar match-data till ett lag-profil.
    Det är detta som matar Dixon-Coles modellen.
    
    Output per lag:
    - avg_xg_for: genomsnittlig xG per match (attack-styrka)
    - avg_xg_against: genomsnittlig xG emot (försvar-styrka)
    - xg_ratio: for/against (>1 = bra lag)
    - over_performance: faktiska mål vs xG (lyckosam eller skicklig?)
    """
    team_stats = {}
    
    for m in match_xg_list:
        home = m["home_team"]
        away = m["away_team"]
        
        # Initialisera lag om de inte finns
        for team in [home, away]:
            if team not in team_stats:
                team_stats[team] = {
                    "matches": 0,
                    "xg_for": 0,
                    "xg_against": 0,
                    "goals_for": 0,
                    "goals_against": 0,
                    "shots_for": 0,
                    "shots_against": 0,
                }
        
        # Hemmalag
        team_stats[home]["matches"] += 1
        team_stats[home]["xg_for"]       += m["home_xg"]
        team_stats[home]["xg_against"]   += m["away_xg"]
        team_stats[home]["goals_for"]    += m["home_score"]
        team_stats[home]["goals_against"]+= m["away_score"]
        team_stats[home]["shots_for"]    += m["home_shots"]
        team_stats[home]["shots_against"]+= m["away_shots"]
        
        # Bortalag
        team_stats[away]["matches"] += 1
        team_stats[away]["xg_for"]       += m["away_xg"]
        team_stats[away]["xg_against"]   += m["home_xg"]
        team_stats[away]["goals_for"]    += m["away_score"]
        team_stats[away]["goals_against"]+= m["home_score"]
        team_stats[away]["shots_for"]    += m["away_shots"]
        team_stats[away]["shots_against"]+= m["home_shots"]
    
    # Beräkna snitt och ratios
    profiles = []
    for team, s in team_stats.items():
        n = max(s["matches"], 1)
        avg_xg_for     = s["xg_for"] / n
        avg_xg_against = s["xg_against"] / n
        goals_for      = s["goals_for"] / n
        goals_against  = s["goals_against"] / n
        
        # Över/under-performance: faktiska mål vs xG
        # >0 = scorer mer än förväntat (lycka ELLER skicklighet)
        # <0 = scorer mindre (otur ELLER ineffektiv)
        overperf_att = goals_for - avg_xg_for
        overperf_def = goals_against - avg_xg_against
        
        profiles.append({
            "team":              team,
            "matches":           s["matches"],
            "avg_xg_for":        round(avg_xg_for, 3),
            "avg_xg_against":    round(avg_xg_against, 3),
            "xg_ratio":          round(avg_xg_for / max(avg_xg_against, 0.01), 3),
            "avg_goals_for":     round(goals_for, 3),
            "avg_goals_against": round(goals_against, 3),
            "overperf_attack":   round(overperf_att, 3),  # + = lyckosar/effektiv
            "overperf_defense":  round(overperf_def, 3),  # - = bra försvar relativt xG
            "avg_shots_for":     round(s["shots_for"] / n, 1),
            "avg_shots_against": round(s["shots_against"] / n, 1),
        })
    
    return sorted(profiles, key=lambda x: x["xg_ratio"], reverse=True)


# ════════════════════════════════════════════
# STEG 4: xG-JUSTERAD MODELL-INPUT
# ════════════════════════════════════════════

def prepare_model_data(match_xg_list):
    """
    Konverterar xG-data till format för Dixon-Coles.
    
    KRITISK SKILLNAD från mål-baserad modell:
    Använder xG som 'virtuella mål' — mer stabilt signal.
    
    Output: DataFrame med kolumner som Dixon-Coles förväntar sig,
    men med xG istället för faktiska mål.
    """
    rows = []
    for m in match_xg_list:
        rows.append({
            "home_team":   m["home_team"],
            "away_team":   m["away_team"],
            "home_goals":  m["home_xg"],    # xG istället för mål!
            "away_goals":  m["away_xg"],
            "home_actual": m["home_score"],  # Behålls för backtesting
            "away_actual": m["away_score"],
            "date":        m["date"],
        })
    return pd.DataFrame(rows)


# ════════════════════════════════════════════
# STEG 5: HUVUD-PIPELINE
# ════════════════════════════════════════════

def run_pipeline(competitions=None, verbose=True):
    """
    Kör hela xG-pipeline.
    
    Returns:
    - match_df: DataFrame med xG per match (input till modellen)
    - team_profiles: lista med lag-statistik
    - raw_match_data: rå match-data med skott-detaljer
    """
    if competitions is None:
        competitions = list(COMPETITIONS.keys())
    
    all_match_xg = []
    
    for comp_key in competitions:
        if verbose:
            print(f"\n📥 Laddar {comp_key}...")
        
        matches = get_matches(comp_key)
        if not matches:
            continue
        
        for i, match in enumerate(matches):
            match_id = match["match_id"]
            home = match["home_team"]["home_team_name"]
            away = match["away_team"]["away_team_name"]
            
            if verbose and i % 10 == 0:
                print(f"  [{i+1}/{len(matches)}] Bearbetar matcher...")
            
            shots = get_match_events(match_id)
            xg_data = extract_xg_from_match(match, shots)
            xg_data["competition"] = comp_key
            all_match_xg.append(xg_data)
    
    if not all_match_xg:
        print("❌ Ingen data hämtad")
        return None, None, None
    
    print(f"\n✅ Totalt {len(all_match_xg)} matcher bearbetade")
    
    # Bygg outputs
    match_df = prepare_model_data(all_match_xg)
    team_profiles = build_team_xg_profile(all_match_xg)
    
    # Spara till CSV
    match_df.to_csv("xg_match_data.csv", index=False)
    pd.DataFrame(team_profiles).to_csv("xg_team_profiles.csv", index=False)
    print("💾 Sparad: xg_match_data.csv + xg_team_profiles.csv")
    
    return match_df, team_profiles, all_match_xg


# ════════════════════════════════════════════
# STEG 6: HJÄLPFUNKTIONER
# ════════════════════════════════════════════

def get_team_xg(team_name_swedish, team_profiles):
    """
    Slå upp ett lags xG-profil med svenska lagnamnet.
    """
    eng_name = TEAM_NAME_MAP.get(team_name_swedish, team_name_swedish)
    
    for p in team_profiles:
        if p["team"] == eng_name:
            return p
    
    print(f"⚠️  Hittade inte: {team_name_swedish} ({eng_name})")
    return None


def predict_match_xg(home_swedish, away_swedish, team_profiles):
    """
    Enkel xG-baserad matchprediktion utan full Dixon-Coles.
    Använd som snabb check eller ensemble-komponent.
    
    Formel: förväntat hem-xG = hem-attack * borta-försvar / liga-snitt
    """
    home_profile = get_team_xg(home_swedish, team_profiles)
    away_profile = get_team_xg(away_swedish, team_profiles)
    
    if not home_profile or not away_profile:
        return None
    
    # Liga-snitt för normalisering
    avg_xg = np.mean([p["avg_xg_for"] for p in team_profiles])
    
    # Justerat xG: hem-attack × borta-försvar / snitt
    home_expected = (home_profile["avg_xg_for"] * away_profile["avg_xg_against"]) / avg_xg
    away_expected = (away_profile["avg_xg_for"] * home_profile["avg_xg_against"]) / avg_xg
    
    # Hemmaplansfördel (+8% empiriskt från VM-data)
    home_expected *= 1.08
    
    return {
        "match":          f"{home_swedish} vs {away_swedish}",
        "home_xg":        round(home_expected, 2),
        "away_xg":        round(away_expected, 2),
        "home_att_rank":  home_profile["avg_xg_for"],
        "away_def_rank":  away_profile["avg_xg_against"],
        "home_overperf":  home_profile["overperf_attack"],
        "away_overperf":  away_profile["overperf_attack"],
        "note": "Hög overperf_attack = laget scorer mer än xG antyder (luck vs skill?)"
    }


def print_top_teams(team_profiles, n=10):
    """Skriver ut topplistan baserat på xG-ratio"""
    print(f"\n{'═'*65}")
    print(f"{'Lag':<20} {'xG-ratio':>8} {'xG/m (for)':>10} {'xG/m (mot)':>10} {'Matcher':>8}")
    print(f"{'═'*65}")
    
    for p in team_profiles[:n]:
        print(f"{p['team']:<20} {p['xg_ratio']:>8.3f} {p['avg_xg_for']:>10.3f} "
              f"{p['avg_xg_against']:>10.3f} {p['matches']:>8}")


# ════════════════════════════════════════════
# KÖR SYSTEMET
# ════════════════════════════════════════════

if __name__ == "__main__":
    
    print("╔══════════════════════════════════════╗")
    print("║     xG DATA PIPELINE — STATSBOMB     ║")
    print("║     VM 2018 + VM 2022                ║")
    print("╚══════════════════════════════════════╝")
    
    # Kör pipeline
    match_df, team_profiles, raw_data = run_pipeline(
        competitions=["WC_2022", "WC_2018"]
    )
    
    if team_profiles:
        # Visa topp-lag
        print("\n🏆 LAG RANKADE EFTER xG-RATIO (attack / försvar)")
        print_top_teams(team_profiles, n=15)
        
        # Exempel-prediceringar för VM 2026 vecka 24
        print("\n⚽ MATCHPREDIKTIONER — VM 2026 VECKA 24")
        print("(Baserat på historisk VM-data 2018+2022)\n")
        
        test_matches = [
            ("Mexiko", "Sydafrika"),
            ("Sydkorea", "Tjeckien"),
            ("Kanada", "Bosnien"),
        ]
        
        for home, away in test_matches:
            pred = predict_match_xg(home, away, team_profiles)
            if pred:
                print(f"  {pred['match']}")
                print(f"    Förväntat xG: {pred['home_xg']} - {pred['away_xg']}")
                if abs(pred['home_overperf']) > 0.1:
                    dir = "över" if pred['home_overperf'] > 0 else "under"
                    print(f"    ⚠️  {home} presterar {dir} sitt xG historiskt")
                print()
        
        print("📊 Data sparad i:")
        print("   xg_match_data.csv    → Input till Dixon-Coles modellen")
        print("   xg_team_profiles.csv → Lag-statistik och ranking")
        print("\n✅ Nästa steg: mata xg_match_data.csv till vmtipset.py")
