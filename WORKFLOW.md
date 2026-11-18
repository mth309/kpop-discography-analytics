# K-Pop DAT & SonicTaste — New User Workflow Guide

This guide walks through the recommended order of operations for building your catalog, training the companion **SonicTaste (STC)** audio model, exploring your analytics, and discovering new artists.

---

## Quickstart Order of Operations

> **First Launch:** K-Pop DAT opens on **Analytics & Trends**, which will be empty until you catalog your first releases. Click **Library Management** in the top navigation bar to get started.

1. **Import & Rate Your First Releases** (`Library Management` → `Import / Export`)
2. **Tidy Profiles & Rosters** (`Library Management` → `Library Explorer`)
3. **Train SonicTaste (STC)** (`Import / Export` → **Sync & Retrain Model**)
4. **Explore Your Analytics** (`Analytics & Trends`)
5. **Scout New Artists** (`Library Management` → `Watchlist Explorer`)

---

## Part 1: Library Management

### 1. Import & Rate Releases (`Import / Export`)
Start by building a baseline of rated tracks (even 3–5 artists gives you immediate analytics, and ~200+ rated tracks gives SonicTaste a solid initial taste profile):

* **Optional — Create the Artist Profile First (`+ new artist`):** Before searching MusicBrainz, you can click **`+ new artist`** right above the Artist Name field to set their Generation, Status, and Group Members or Parent Groups up front (instead of circling back later). Saving the profile also pre-fills the artist's name in the import bar for you.
* **Single Album:** Enter an artist and release title to stage a single EP, LP, or Single from MusicBrainz.
* **Bulk Discography:** Enter an artist to load their official release history chronologically (already-imported releases are flagged), then step through them one by one.
* **In the Staging Table:**
  * Score tracks (`1.0–5.0★`) and check **Title** for promoted title tracks.
  * Verify **Track Type** (`Core`, `Solo`, `Unit`, `Alt Lang` for Japanese/English/Chinese versions, or `Remix / Inst / Skit` for non-canonical tracks). *Only substantive tracks (`Core`, `Solo`, `Unit`) count toward your core analytics and SonicTaste training.*
  * **Auto-Enrichment:** When a previously rated single reappears on a later EP or LP, K-Pop DAT automatically pre-fills both its **Title** flag and your prior **Rating**.

### 2. Manage Artists, Rosters & Inline Ratings (`Library Explorer`)
* **Artist Directory:** Click any artist to edit their metadata **Generation**, **Status** (`Active` / `Disbanded`), **Group Members** for groups or **Parent Groups** for soloists. Former members and groups should be prefixed with `*`. Linking soloists to parent groups automatically aggregates their `Solo` and `Unit` tracks from group releases.
* **Tracks View:** Filter by artist, track type, rating, or release window and adjust ratings inline—no need to re-open the full album editor.

### 3. Sync & Train SonicTaste (`Sync Prediction Model Embeddings`)
Once you have imported and rated new releases:

1. In `Library Management` → `Import / Export`, click **Sync & Retrain Model** (in the **Sync Prediction Model Embeddings** card).
2. SonicTaste fetches 30-second audio previews for newly rated canonical tracks, extracts MERT embeddings + 5-second hook-contrast windows, and updates your saved model checkpoint.
   * *Note:* If you only **edit existing ratings** in Library Explorer, you do **not** need to re-sync—SonicTaste detects rating changes and retrains its prediction head automatically in ~2 seconds on the next prediction.
   * *Unmatched Previews:* Any tracks that could not be matched automatically are listed in the status banner and `data/embeddings/unmatched_tracks.csv`; you can optionally supply local audio files in `SonicTaste/data/audio_overrides/{track_id}.mp3` and re-sync.

### 4. Backups & Restore (`Export & Backup Library`)
* **Create Snapshot:** Saves a timestamped, WAL-checkpointed copy of your database and app files to `backups/snapshots/`.
* **Download Backups:** Export raw SQLite (`.db`), structured multi-table `.json`, or flattened `.csv`.
* **Restore Catalog (`.db` / `.json`):** Drop a backup file to restore your entire catalog atomically (an automatic `pre_restore` snapshot is created first).

---

## Part 2: Analytics & Discovery

Switch to **`Analytics & Trends`** once you have a few artists cataloged:

### 1. Artist Deep Dive
Select any artist to inspect:
* **Quality-Weighted Wins Above Replacement (Q-WAR) Metrics:**  
**Compacted Q-WAR** (volume-dampened cumulative quality) and **Q-WAR Rate** (quality density per track), with automatic rookie gating for catalogs under 12 tracks.
* **Career Trajectory Chart:** Toggle between **Avg. Track Score**, **Q-WAR**, and **Rate**, and switch the X-axis between **Calendar Timeline** (shows productivity and release gaps over time) and **Release Sequence** (lines up substantive EPs/LPs `#1, #2, #3...` cleanly).
* **Title Track vs. B-Side Split:** Side-by-side comparison of how promoted title tracks score relative to album B-sides.
* **Rating Distribution Histogram:** Visual breakdown of your `1.0★–5.0★` scores across their catalog.


*(Note: Advance singles, repackages, `alt_lang`, and `non_canon` tracks are automatically deduplicated behind the scenes so tracks are never double-counted in any of these metrics.)*

### 2. Multi-Artist Comparison
Compare up to **4 artists'** trajectories side by side across career averages, Q-WAR, and Q-WAR Rate (in either Calendar or Release Sequence alignment).

### 3. All Artist Dashboards
View your library-wide rankings for **Groups** and **Soloists**:
* **Top-5 Category Leaders:** `5★ Rate`, `Title Track Avg`, `B-Side Avg`, and `Best Album`.
* **Tier List:** Automatically ranks artists with `≥ 12` tracks by a composite Q-WAR + Rate score, with rookies held on a Provisional shelf until they cross 12 tracks.

### 4. Scout New Artists (`Watchlist Explorer`)
Once SonicTaste has been synced with your rated library:

1. Go to `Library Management` → `Watchlist Explorer` and add prospective artists you want to check out.
2. Click **✨ Predict** on any row (or **↻** in the **Prediction** column header to refresh the entire list).
3. Hover over a prediction badge (`e.g., 4.12★ (7/10)`) to see the top predicted tracks and Hit probabilities, and check the `YYYY-MM-DD` date underneath to see when it was last scored.
4. When a prospect looks promising, click **📥 Import** to jump straight into importing their discography. Alternatively, committing any release to a watchlist artist automatically graduates them from the watchlist into your main library.
