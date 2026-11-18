# K-Pop Discography & Analytics Tracker

A local FastAPI + SQLite web application (**K-Pop DAT**) for cataloging K-Pop discographies, rating individual tracks (`1.0–5.0★`), and analyzing career trajectories across groups and soloists. Integrates optionally with the companion **[SonicTaste Classifier (STC)](https://github.com/mth309/kpop-ml-sonictaste)** audio ML sidecar for Watchlist taste predictions.

> **New here?** See the **[New User Workflow Guide](WORKFLOW.md)** for the recommended step-by-step order from your first import to SonicTaste predictions.

---

## Key Features

* **MusicBrainz Ingestion (Single & Bulk):** Fetch official releases and tracklists from MusicBrainz, auto-categorize tracks under a 5-tier model (`core`, `solo`, `unit`, `alt_lang`, `non_canon`), and auto-fill `is_title_track` and prior `rating` values when tracks reappear on EPs/LPs.
* **Canonical Track Deduplication (`CANONICAL_UNIQUE_TRACK_SQL`):** Ensures re-releases, repackages, advance singles (within a 120-day pre-release window), `alt_lang`, and `non_canon` tracks never double-count or distort catalog metrics.
* **Group & Soloist Analytics:**
  * **Q-WAR & Quality Density:** Cumulative volume-dampened Quality-Weighted WAR (`Compacted Q-WAR`) and listening density (`Q-WAR / Track`), with rookie `<12`-track gating.
  * **Artist Deep Dive:** Volume-weighted EMA & cumulative Q-WAR trajectories with **Calendar Timeline** vs. **Substantive Release Sequence** views, rating histograms, and standout tracks.
  * **Tier Lists & Category Leaders:** S/A/B/C/D + Provisional tier lists and Top-5 leaderboards (`5★ Rate`, `Title Track Avg`, `B-Side Avg`, `Best Album`) for both Groups and Soloists.
  * **Multi-Artist Compare:** Side-by-side comparison of up to 4 artists across scores, Q-WAR, and trajectories.
* **Roster & Watchlist Explorer:**
  * Two-way group member roster & soloist parent-group linking (including `*` former member markers and `solo`/`unit` track aggregation).
  * Discovery Watchlist with optional **SonicTaste** compatibility predictions (`✨ Predict`, batch `↻` refresh, and `predicted_at` date tracking) and automatic graduation upon album import.
* **Backups & Exports:** One-click WAL-checkpointed snapshots (`backup.py`) plus full `.db`, `.json`, and formula-sanitized `.csv` downloads.

---

## Setup & Running

### 1. Create a Virtual Environment & Install Dependencies
```powershell
cd "KPop DAT"
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Start the Web Server
```powershell
python app.py
```
Then open **http://127.0.0.1:8000** in your browser. On first launch, `schema.sql` automatically initializes a fresh `kpop_ratings.db` SQLite database.

### 3. Optional Environment Variables
* `KPOP_APP_PORT` — Web server port (default: `8000`).
* `MB_USER_AGENT` — Custom MusicBrainz `User-Agent` contact string.
* `STC_SERVER_PORT` — Port for the companion SonicTaste (STC) sidecar (default: `5001`; `MTC_SERVER_PORT` also supported).
* `STC_PYTHON_EXE` — Path to the SonicTaste Python virtual environment (auto-discovered if `STC App`/`SonicTaste`/`MTC App` has a `venv`/`.venv`, or `~/venvs/stc` / `~/venvs/mtc` exists; `MTC_PYTHON_EXE` also supported).

---

## Running Tests

All unit tests run against isolated temporary databases in `%TEMP%` and never touch `kpop_ratings.db`:

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
```
