"""
VM 2026 — Auto Update System
==============================
Kör detta script inför varje omgång:
    python3 update.py

Det kommer att:
1. Hämta kommande VM-matcher från football-data.org
2. Hämta xG-data från Statsbomb (gratis)
3. Köra Dixon-Coles modellen
4. Uppdatera dashboard med nya matcher och odds
5. Scrapa Svenska Spels streckning

Krav: pip install requests pandas numpy scipy beautifulsoup4
"""

import requests
import pandas as pd
import numpy as np
from scipy.stats import poisson
from scipy.optimize import minimize
from bs4 import BeautifulSoup
import json
import time
import os
from pathlib import Path
from datetime import datetime

# ════════════════════════════════════════
# CONFIG — ÄNDRA DESSA
# ════════════════════════════════════════
# OBS: nyckeln läses från miljövariabeln FOOTBALL_API_KEY (GitHub Secret),
# läggs ALDRIG i klartext i koden — repot är publikt.
API_KEY = os.environ.get("FOOTBALL_API_KEY", "")
BANKROLL = 5000          # Standard bankroll kr
KELLY_FRACTION = 0.25    # 1/4 Kelly

if not API_KEY:
    print("⚠️  FOOTBALL_API_KEY saknas i miljövariabler — kollar GitHub Secrets / lokal .env")

# Paths
BASE_DIR = Path(__file__).parent
CACHE_DIR = BASE_DIR / "xg_cache"
CACHE_DIR.mkdir(exist_ok=True)

# ════════════════════════════════════════
# VM-TIPSET OMGÅNGSSCHEMA (Svenska Spel, 2026)
# ════════════════════════════════════════
# Källa: spela.svenskaspel.se/fotboll/vm/vm-tipset
# 5 fasta omgångar med spelstopp 18:59 svensk tid.
# OBS: om Svenska Spel ändrar datumen måste denna lista uppdateras manuellt.
VM_TIPSET_ROUNDS = [
    {"round": 1, "deadline_date": "2026-06-11", "deadline_time": "20:59"},  # premiärdagen, annan stopptid
    {"round": 2, "deadline_date": "2026-06-17", "deadline_time": "18:59"},
    {"round": 3, "deadline_date": "2026-06-22", "deadline_time": "18:59"},
    {"round": 4, "deadline_date": "2026-06-25", "deadline_time": "18:59"},
    {"round": 5, "deadline_date": "2026-06-29", "deadline_time": "18:59"},
]


def get_current_round_info():
    """
    Räknar ut vilken VM-tipset-omgång som är aktuell just nu, och dess deadline.

    Logik: hitta första omgången vars deadline ännu inte passerat.
    Om alla 5 omgångar är förbi, returnera den sista (VM-tipset är slut).

    Returnerar: (round_num, deadline_str) t.ex. (3, "22 jun 18:59")
    """
    now = datetime.now()
    MONTH_SV = {1: "jan", 2: "feb", 3: "mar", 4: "apr", 5: "maj", 6: "jun",
                7: "jul", 8: "aug", 9: "sep", 10: "okt", 11: "nov", 12: "dec"}

    for r in VM_TIPSET_ROUNDS:
        deadline_dt = datetime.strptime(
            f"{r['deadline_date']} {r['deadline_time']}", "%Y-%m-%d %H:%M"
        )
        if now < deadline_dt:
            deadline_str = f"{deadline_dt.day} {MONTH_SV[deadline_dt.month]} {r['deadline_time']}"
            return r["round"], deadline_str

    # Alla omgångar har passerat — visa sista omgången som referens
    last = VM_TIPSET_ROUNDS[-1]
    deadline_dt = datetime.strptime(
        f"{last['deadline_date']} {last['deadline_time']}", "%Y-%m-%d %H:%M"
    )
    deadline_str = f"{deadline_dt.day} {MONTH_SV[deadline_dt.month]} {last['deadline_time']} (avslutat)"
    return last["round"], deadline_str



# ════════════════════════════════════════
# STEG 1: HÄMTA VM-MATCHER
# ════════════════════════════════════════

def get_upcoming_wc_matches():
    """Hämtar kommande VM-matcher från football-data.org"""
    print("📡 Hämtar VM-matcher från football-data.org...")
    
    headers = {"X-Auth-Token": API_KEY}
    url = "https://api.football-data.org/v4/competitions/WC/matches"
    
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            data = r.json()
            upcoming = []
            for m in data.get("matches", []):
                if m["status"] in ["SCHEDULED", "TIMED"]:
                    upcoming.append({
                        "home": m["homeTeam"]["name"],
                        "away": m["awayTeam"]["name"],
                        "date": m["utcDate"],
                        "matchday": m.get("matchday", 0)
                    })
            print(f"  ✅ {len(upcoming)} kommande matcher hittade")
            return upcoming
        else:
            print(f"  ⚠️  API svarade {r.status_code} — använder cached data")
            return []
    except Exception as e:
        print(f"  ⚠️  football-data.org ej nåbart — använder matches_config.json")
        return []


def get_finished_wc_matches():
    """Hämtar avslutade VM-matcher för modell-träning"""
    print("📡 Hämtar avslutade VM-matcher...")
    
    headers = {"X-Auth-Token": API_KEY}
    url = "https://api.football-data.org/v4/competitions/WC/matches?status=FINISHED"
    
    r = requests.get(url, headers=headers, timeout=15)
    
    if r.status_code == 200:
        data = r.json()
        matches = []
        for m in data.get("matches", []):
            score = m.get("score", {}).get("fullTime", {})
            if score.get("home") is not None:
                matches.append({
                    "home_team": m["homeTeam"]["name"],
                    "away_team": m["awayTeam"]["name"],
                    "home_goals": score["home"],
                    "away_goals": score["away"],
                    "date": m["utcDate"]
                })
        print(f"  ✅ {len(matches)} avslutade VM 2026-matcher")
        return pd.DataFrame(matches) if matches else None
    else:
        print(f"  ⚠️  Ingen live VM-data — använder VM 2018+2022")
        return None


# ════════════════════════════════════════
# STEG 2: xG-DATA FRÅN STATSBOMB
# ════════════════════════════════════════

BASE_URL = "https://raw.githubusercontent.com/statsbomb/open-data/master/data"

COMPETITIONS = {
    "WC_2022": {"competition_id": 43, "season_id": 106},
    "WC_2018": {"competition_id": 43, "season_id": 3},
}

def fetch_json(url, cache_file=None):
    if cache_file and Path(cache_file).exists():
        with open(cache_file) as f:
            return json.load(f)
    time.sleep(0.3)
    r = requests.get(url, headers={"User-Agent": "VM2026/1.0"}, timeout=30)
    if r.status_code != 200:
        return None
    data = r.json()
    if cache_file:
        with open(cache_file, 'w') as f:
            json.dump(data, f)
    return data

def get_xg_data():
    """Hämtar xG-data från Statsbomb för VM 2018 + 2022"""
    print("📊 Hämtar xG-data från Statsbomb...")
    
    # Kolla om vi redan har cached data
    cache_file = BASE_DIR / "xg_match_data.csv"
    if cache_file.exists():
        df = pd.read_csv(cache_file)
        print(f"  ✅ Laddar cached xG-data: {len(df)} matcher")
        return df
    
    print("  🔄 Ingen cache — hämtar från Statsbomb GitHub...")
    all_matches = []
    
    for comp_key, comp in COMPETITIONS.items():
        cid, sid = comp["competition_id"], comp["season_id"]
        url = f"{BASE_URL}/matches/{cid}/{sid}.json"
        cache = CACHE_DIR / f"matches_{comp_key}.json"
        
        matches = fetch_json(url, cache)
        if not matches:
            continue
        
        print(f"  [{comp_key}] {len(matches)} matcher")
        
        for i, match in enumerate(matches):
            match_id = match["match_id"]
            ev_cache = CACHE_DIR / f"events_{match_id}.json"
            
            ev_url = f"{BASE_URL}/events/{match_id}.json"
            events = fetch_json(ev_url, ev_cache)
            if not events:
                continue
            
            shots = [e for e in events if e.get("type", {}).get("name") == "Shot"]
            
            home = match["home_team"]["home_team_name"]
            away = match["away_team"]["away_team_name"]
            
            home_xg = sum(s.get("shot",{}).get("statsbomb_xg",0) or 0 for s in shots if s.get("team",{}).get("name") == home)
            away_xg = sum(s.get("shot",{}).get("statsbomb_xg",0) or 0 for s in shots if s.get("team",{}).get("name") == away)
            
            all_matches.append({
                "home_team": home, "away_team": away,
                "home_goals": home_xg, "away_goals": away_xg,
                "home_actual": match["home_score"],
                "away_actual": match["away_score"],
                "date": match["match_date"],
                "competition": comp_key
            })
    
    df = pd.DataFrame(all_matches)
    df.to_csv(cache_file, index=False)
    print(f"  ✅ {len(df)} matcher med xG-data sparad")
    return df


# ════════════════════════════════════════
# STEG 3: LAG-PROFILER
# ════════════════════════════════════════

def build_team_profiles(df):
    """Bygger xG-profil per lag"""
    teams = {}
    for _, row in df.iterrows():
        for team, xg_for, xg_against in [
            (row["home_team"], row["home_goals"], row["away_goals"]),
            (row["away_team"], row["away_goals"], row["home_goals"])
        ]:
            if team not in teams:
                teams[team] = {"n": 0, "xg_for": 0, "xg_against": 0}
            teams[team]["n"] += 1
            teams[team]["xg_for"] += xg_for
            teams[team]["xg_against"] += xg_against
    
    profiles = {}
    for team, s in teams.items():
        n = max(s["n"], 1)
        profiles[team] = {
            "avg_xg_for": s["xg_for"] / n,
            "avg_xg_against": s["xg_against"] / n,
            "matches": s["n"]
        }
    
    return profiles


# ════════════════════════════════════════
# STEG 4: MODELL
# ════════════════════════════════════════

def predict_match(home, away, profiles, avg_xg):
    """Dixon-Coles Poisson prediction"""
    # OBS: lag som saknas i xG-databasen (oftast VM-debutanter eller lag
    # som missade 2018/2022) får ett konservativt svagt default-värde,
    # för att undvika att modellen ger dem orealistiskt höga vinstchanser.
    # Detta är en approximation — se "quality"-flaggan i predictions för
    # att identifiera matcher där minst ett lag saknar riktig xG-data.
    DEFAULT = {"avg_xg_for": avg_xg * 0.40, "avg_xg_against": avg_xg * 1.60}
    
    hp = profiles.get(home, DEFAULT)
    ap = profiles.get(away, DEFAULT)
    
    lh = (hp["avg_xg_for"] * ap["avg_xg_against"] / avg_xg) * 1.08
    la = (ap["avg_xg_for"] * hp["avg_xg_against"] / avg_xg)
    
    matrix = np.zeros((9, 9))
    for i in range(9):
        for j in range(9):
            matrix[i, j] = poisson.pmf(i, lh) * poisson.pmf(j, la)
    
    return {
        "home_win": float(np.sum(np.tril(matrix, -1))),
        "draw":     float(np.sum(np.diag(matrix))),
        "away_win": float(np.sum(np.triu(matrix, 1))),
        "xg_h": round(lh, 2),
        "xg_a": round(la, 2),
        "h_known": home in profiles,
        "a_known": away in profiles
    }


def get_round_window(round_num):
    """
    Räknar ut datumfönstret (start, slut) för en given VM-tipset-omgång.
    Fönstret är: dagen efter föregående omgångs deadline, till och med denna omgångs deadline.
    Omgång 1 räknas från VM-start (2026-06-11).
    """
    idx = next((i for i, r in enumerate(VM_TIPSET_ROUNDS) if r["round"] == round_num), 0)
    end_dt = datetime.strptime(
        f"{VM_TIPSET_ROUNDS[idx]['deadline_date']} {VM_TIPSET_ROUNDS[idx]['deadline_time']}",
        "%Y-%m-%d %H:%M"
    )
    if idx == 0:
        start_dt = datetime.strptime("2026-06-11 00:00", "%Y-%m-%d %H:%M")
    else:
        prev = VM_TIPSET_ROUNDS[idx - 1]
        start_dt = datetime.strptime(
            f"{prev['deadline_date']} {prev['deadline_time']}", "%Y-%m-%d %H:%M"
        )
    return start_dt, end_dt


def select_matches_for_round(odds_data, round_num, max_matches=13):
    """
    Väljer de N närmast kommande VM-matcherna räknat från NU,
    sorterade på avsparkstid. Detta är INTE nödvändigtvis exakt
    samma matcher som Svenska Spel valt för sitt VM-tipset, men
    garanterar en fylld lista med aktuella, riktiga VM-matcher
    och riktiga odds — oavsett var i omgångscykeln vi befinner oss.

    Matcher som redan startat/spelats filtreras bort (de saknar
    värde för betting-syften framåt).
    """
    now = datetime.now()
    upcoming = []

    for m in odds_data:
        date_str = m.get("date", "")
        if not date_str:
            continue
        try:
            match_dt = datetime.strptime(date_str[:16], "%Y-%m-%dT%H:%M")
        except ValueError:
            continue
        if match_dt > now:
            upcoming.append(m)

    upcoming.sort(key=lambda m: m.get("date", ""))
    print(f"     🔍 {len(upcoming)} kommande matcher hittade (av {len(odds_data)} totalt), tar de {max_matches} närmaste")
    return upcoming[:max_matches]


# ════════════════════════════════════════
# STEG 5: ODDS (manuell input eller scraping)
# ════════════════════════════════════════

def get_odds_for_matches(matches):
    """
    Hämtar odds automatiskt från the-odds-api.com.
    Filtrerar till matcher inom AKTUELL omgångs datumfönster och
    byter ut hela matchlistan (inte bara merge av odds på gamla lag).
    Faller tillbaka på befintlig matches_config.json om API ej tillgängligt
    eller ingen match hittas i fönstret.
    """
    print("📈 Hämtar odds automatiskt...")

    try:
        from odds_fetcher import get_wc_odds
        odds_data = get_wc_odds()
        if odds_data:
            round_num, _ = get_current_round_info()
            selected = select_matches_for_round(odds_data, round_num)

            if not selected:
                print(f"  ⚠️  Inga matcher hittades för omgång {round_num} i tidsfönstret")
                print("  ℹ️  Använder befintliga matcher från matches_config.json")
                return matches

            # Numrera om 1-N och bygg om configen i samma format som tidigare
            new_matches = []
            for i, m in enumerate(selected, start=1):
                new_matches.append({
                    "nr": i,
                    "home": m["home"], "away": m["away"],
                    "home_sv": m["home_sv"], "away_sv": m["away_sv"],
                    "odds_h": m["odds_h"], "odds_d": m["odds_d"], "odds_a": m["odds_a"],
                })

            config_file = BASE_DIR / "matches_config.json"
            with open(config_file, "w", encoding="utf-8") as f:
                json.dump({"matches": new_matches}, f, indent=2, ensure_ascii=False)

            print(f"  ✅ matches_config.json uppdaterad — {len(new_matches)} matcher för omgång {round_num}")
            return new_matches
        else:
            print("  ℹ️  Använder befintliga odds från matches_config.json")
            return matches
    except ImportError:
        print("  ⚠️  odds_fetcher.py saknas — använder manual odds")
        return matches


# ════════════════════════════════════════
# STEG 6: EV & KELLY
# ════════════════════════════════════════

def calculate_ev_kelly(prob, odds):
    ev = (prob * odds) - 1
    b = odds - 1
    k = (b * prob - (1 - prob)) / b
    return round(ev, 4), round(max(0, k * KELLY_FRACTION), 4)


# ════════════════════════════════════════
# STEG 7: SVENSKA SPEL STRECKNING
# ════════════════════════════════════════

def get_streckning():
    """Hämtar live-streckning från Svenska Spel VM-tipset"""
    print("📊 Hämtar streckning från Svenska Spel...")
    
    urls = [
        "https://www.svenskaspel.se/api/game/draws?gameTypes=VMTIPSET",
        "https://spela.svenskaspel.se/europatipset",
    ]
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/html",
    }
    
    for url in urls:
        try:
            r = requests.get(url, headers=headers, timeout=10)
            if r.status_code == 200:
                if "application/json" in r.headers.get("content-type", ""):
                    data = r.json()
                    streckning = {}
                    for draw in data.get("draws", [{}]):
                        for event in draw.get("drawEvents", []):
                            match = event.get("eventDescription", "")
                            dist = event.get("distribution", {})
                            streckning[match] = {
                                "hemma": dist.get("home", "?"),
                                "kryss": dist.get("draw", "?"),
                                "borta": dist.get("away", "?")
                            }
                    if streckning:
                        print(f"  ✅ Streckning hämtad: {len(streckning)} matcher")
                        return streckning
        except Exception as e:
            pass
    
    print("  ⚠️  Kunde inte hämta streckning — uppdatera manuellt i dashboarden")
    return {}


# ════════════════════════════════════════
# STEG 8: GENERERA PREDICTIONS JSON
# ════════════════════════════════════════

def generate_predictions(matches_config, profiles, avg_xg, streckning={}):
    """
    Genererar predictions för alla matcher.
    matches_config: lista med {home, away, odds_h, odds_d, odds_a}
    """
    results = []
    
    for m in matches_config:
        p = predict_match(m["home"], m["away"], profiles, avg_xg)
        
        ev_h, k_h = calculate_ev_kelly(p["home_win"], m.get("odds_h", 2.0))
        ev_d, k_d = calculate_ev_kelly(p["draw"],     m.get("odds_d", 3.5))
        ev_a, k_a = calculate_ev_kelly(p["away_win"], m.get("odds_a", 3.0))
        
        quality = "full" if p["h_known"] and p["a_known"] else \
                  "half" if p["h_known"] or p["a_known"] else "none"
        
        match_str = f"{m.get('home_sv', m['home'])} - {m.get('away_sv', m['away'])}"
        streck = streckning.get(match_str, {})
        
        results.append({
            "nr":      m.get("nr", 0),
            "match":   match_str,
            "xg_h":    p["xg_h"],
            "xg_a":    p["xg_a"],
            "prob_h":  round(p["home_win"], 3),
            "prob_d":  round(p["draw"], 3),
            "prob_a":  round(p["away_win"], 3),
            "odds_h":  m.get("odds_h", 0),
            "odds_d":  m.get("odds_d", 0),
            "odds_a":  m.get("odds_a", 0),
            "ev_h":    ev_h, "ev_d": ev_d, "ev_a": ev_a,
            "kelly_h": k_h,  "kelly_d": k_d, "kelly_a": k_a,
            "quality": quality,
            "streckning_h": streck.get("hemma", "?"),
            "streckning_d": streck.get("kryss", "?"),
            "streckning_a": streck.get("borta", "?"),
        })
    
    return results


# ════════════════════════════════════════
# STEG 9: SPARA CONFIG-MALL
# ════════════════════════════════════════

def save_matches_config_template(upcoming_matches):
    """Sparar en config-fil som användaren kan fylla i med odds"""
    config_file = BASE_DIR / "matches_config.json"
    
    if config_file.exists():
        print(f"  ℹ️  matches_config.json finns redan — uppdaterar inte")
        return
    
    template = []
    for i, m in enumerate(upcoming_matches[:13], 1):
        template.append({
            "nr": i,
            "home": m["home"],
            "away": m["away"],
            "home_sv": m["home"],
            "away_sv": m["away"],
            "odds_h": 2.00,
            "odds_d": 3.40,
            "odds_a": 3.50,
            "date": m.get("date", "")
        })
    
    with open(config_file, 'w', encoding='utf-8') as f:
        json.dump(template, f, indent=2, ensure_ascii=False)
    
    print(f"  ✅ matches_config.json skapad — uppdatera odds i filen!")


def load_matches_config():
    """Laddar matches_config.json"""
    config_file = BASE_DIR / "matches_config.json"
    if not config_file.exists():
        print("  ❌ matches_config.json saknas — kör update.py igen")
        return []
    with open(config_file) as f:
        return json.load(f)


# ════════════════════════════════════════
# STEG 10: UPPDATERA DASHBOARD
# ════════════════════════════════════════

def update_dashboard(predictions, round_num=1, deadline="11 jun 20:59"):
    """Injicerar ny data i dashboard HTML-filen"""
    
    # Spara predictions som JSON
    pred_file = BASE_DIR / "predictions.json"
    with open(pred_file, 'w', encoding='utf-8') as f:
        json.dump({
            "round": round_num,
            "deadline": deadline,
            "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "matches": predictions
        }, f, indent=2, ensure_ascii=False)
    
    print(f"  ✅ predictions.json uppdaterad med {len(predictions)} matcher")
    
    # Uppdatera MATCHES-konstanten i dashboard HTML
    # index.html ligger i repo-root (inte i 1/) för att GitHub Pages ska kunna serva den
    dashboard_file = BASE_DIR.parent / "index.html"
    if not dashboard_file.exists():
        print("  ⚠️  index.html saknas")
        return
    
    with open(dashboard_file, 'r', encoding='utf-8') as f:
        html = f.read()
    
    # Bygg ny MATCHES JS-array
    new_matches_js = "const MATCHES = " + json.dumps(predictions, ensure_ascii=False) + ";"
    
    # Ersätt gammal
    import re
    html = re.sub(r'const MATCHES = \[.*?\];', new_matches_js, html, flags=re.DOTALL)
    
    # Uppdatera omgång och deadline (robust regex — matchar oavsett tidigare värde)
    html = re.sub(
        r'Omgång <strong>\d+</strong>',
        f'Omgång <strong>{round_num}</strong>',
        html
    )
    html = re.sub(
        r'Stopp <strong>.*?</strong>',
        f'Stopp <strong>{deadline}</strong>',
        html
    )
    
    with open(dashboard_file, 'w', encoding='utf-8') as f:
        f.write(html)
    
    print(f"  ✅ index.html uppdaterad — Omgång {round_num}")


# ════════════════════════════════════════
# MAIN — KÖR ALLT
# ════════════════════════════════════════

def main():
    print()
    print("╔══════════════════════════════════════════════╗")
    print("║      VM 2026 — AUTO UPDATE SYSTEM           ║")
    print(f"║      {datetime.now().strftime('%Y-%m-%d %H:%M')}                        ║")
    print("╚══════════════════════════════════════════════╝")
    print()
    
    # 1. Hämta xG-data
    xg_df = get_xg_data()
    profiles = build_team_profiles(xg_df)
    avg_xg = xg_df["home_goals"].mean()
    print(f"  Lag i databasen: {len(profiles)}")
    print(f"  Snitt xG: {avg_xg:.3f}")
    print()
    
    # 2. Hämta kommande matcher
    upcoming = get_upcoming_wc_matches()
    if upcoming:
        save_matches_config_template(upcoming)
    print()
    
    # 3. Ladda match-config med odds
    matches = load_matches_config()
    if not matches:
        print("❌ Inga matcher att analysera.")
        print("   Uppdatera matches_config.json med rätt lag och odds.")
        return
    print(f"  ✅ {len(matches)} matcher laddade från config")
    print()

    # 3b. Försök hämta/uppdatera matchlistan automatiskt för AKTUELL omgång
    # (byter ut gamla omgångens matcher om the-odds-api har färska matcher
    # inom tidsfönstret — annars behålls matches oförändrad)
    matches = get_odds_for_matches(matches)
    print()
    
    # 4. Hämta streckning
    streckning = get_streckning()
    print()
    
    # 5. Kör modellen
    print("🧠 Kör Dixon-Coles modell...")
    predictions = generate_predictions(matches, profiles, avg_xg, streckning)
    
    # Visa resultat
    print()
    print(f"{'Match':<30} {'Modell':^20} {'Bästa EV':>10}")
    print("─" * 62)
    for p in predictions:
        evs = {"H": p["ev_h"], "X": p["ev_d"], "B": p["ev_a"]}
        best = max(evs, key=evs.get)
        best_ev = evs[best]
        model_str = f"H:{p['prob_h']:.0%} X:{p['prob_d']:.0%} B:{p['prob_a']:.0%}"
        ev_str = f"{best} {best_ev:+.0%}" if best_ev > 0.03 else "ingen edge"
        print(f"  {p['match']:<28} {model_str:^20} {ev_str:>10}")
    
    # 6. Uppdatera dashboard
    print()
    print("💾 Uppdaterar dashboard...")
    round_num, deadline = get_current_round_info()
    print(f"  📅 VM-tipset omgång {round_num}, deadline: {deadline}")
    update_dashboard(predictions, round_num=round_num, deadline=deadline)
    
    print()
    print("╔══════════════════════════════════════════════╗")
    print("║  ✅ KLAR! Öppna index.html                   ║")
    print("╚══════════════════════════════════════════════╝")
    print()


if __name__ == "__main__":
    main()
