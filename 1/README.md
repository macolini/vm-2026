# VM 2026 — Betting System

## Filstruktur
```
vm2026/
├── vm2026_dashboard.html   → Öppna i Chrome (fungerar offline)
├── update.py               → Kör inför varje omgång
├── matches_config.json     → Uppdatera med nya matcher och odds
├── xg_pipeline.py          → Hämtar xG-data från Statsbomb
├── xg_match_data.csv       → 128 VM-matcher med xG
└── xg_team_profiles.csv    → Lag-profiler
```

## Installation (en gång)
```bash
pip install requests pandas numpy scipy beautifulsoup4
```

## Inför varje omgång
```bash
# 1. Uppdatera matches_config.json med nya matcher och odds
# 2. Kör:
python3 update.py

# 3. Öppna vm2026_dashboard.html i Chrome
```

## Uppdatera matches_config.json
Öppna filen och ändra:
- home/away: engelska lagnamn (för modellen)
- home_sv/away_sv: svenska lagnamn (för dashboarden)
- odds_h/odds_d/odds_a: aktuella odds från spelbolaget

## Syndikatsystem (VM-tipset)
Spelare A: Favorit-profil (tecken i dashboarden)
Spelare B: Kryss-profil
Spelare C: Skräll-profil
Insats: 192 kr var

## Kombibett
Alla tre lägger samma trettonling på eget konto.
Totala odds: ~547.8x
Rekommenderad insats: 100 kr

## Omgångar
1. 11 juni → ✅ Klart
2. 17 juni → Uppdatera matches_config.json
3. 22 juni
4. 25 juni
5. 29 juni
