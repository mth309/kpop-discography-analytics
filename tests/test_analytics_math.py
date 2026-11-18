import os
import tempfile
import unittest
from datetime import date

import database

TEST_DB_FILE = os.path.join(tempfile.gettempdir(), "test_analytics_math.db")

def setUpModule():
    database.DB_NAME = TEST_DB_FILE
    database.init_db()

def tearDownModule():
    database.DB_NAME = str(database.BASE_DIR / "kpop_ratings.db")
    for suffix in ["", "-wal", "-shm"]:
        p = TEST_DB_FILE + suffix
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass


class TestAnalyticsMath(unittest.TestCase):
    """
    Backlog Item #9: Regression & edge-case test suite for analytics and Q-WAR math engines.
    """

    def test_qwar_zero_or_negative_cases(self):
        """0 tracks, None avg_rating, or all skips must safely return 0.0 with no zero-division."""
        # 0 tracks
        res = database.calculate_qwar_metrics(fives=0, fours=0, threes=0, skips=0, total_tracks=0, avg_rating=None)
        self.assertEqual(res, {"compacted_qwar": 0.0, "legacy_qwar": 0.0, "qwar_per_track": 0.0})

        # All skips resulting in negative raw points
        res_skips = database.calculate_qwar_metrics(fives=0, fours=0, threes=0, skips=5, total_tracks=5, avg_rating=1.0)
        self.assertEqual(res_skips, {"compacted_qwar": 0.0, "legacy_qwar": 0.0, "qwar_per_track": 0.0})

        # None avg_rating
        res_none_avg = database.calculate_qwar_metrics(fives=5, fours=0, threes=0, skips=0, total_tracks=5, avg_rating=None)
        self.assertEqual(res_none_avg, {"compacted_qwar": 0.0, "legacy_qwar": 0.0, "qwar_per_track": 0.0})

    def test_qwar_rookie_gate_scaling(self):
        """Catalogs with <12 tracks must apply the linear gate (total_tracks / 12.0)."""
        # Exactly 12 tracks: gate = 1.0
        res_12 = database.calculate_qwar_metrics(fives=12, fours=0, threes=0, skips=0, total_tracks=12, avg_rating=5.0)
        # Exactly 6 tracks: gate = 0.5
        res_6 = database.calculate_qwar_metrics(fives=6, fours=0, threes=0, skips=0, total_tracks=6, avg_rating=5.0)

        # Gate scales uncompacted legacy_qwar linearly:
        # raw points for 12 fives = 36. raw points for 6 fives = 18.
        # multiplier for 5.0 avg is (5.0/4.0)^1.5 = 1.39754...
        # legacy_qwar(12) = 36 * 1.39754 * 1.0 = ~50.31
        # legacy_qwar(6) = 18 * 1.39754 * 0.5 = ~12.58
        self.assertAlmostEqual(res_12["legacy_qwar"], 50.31, places=1)
        self.assertAlmostEqual(res_6["legacy_qwar"], 12.58, places=1)
        self.assertGreater(res_12["compacted_qwar"], res_6["compacted_qwar"])

    def test_qwar_listening_density_calculation(self):
        """qwar_per_track correctly computes legacy_qwar / total_tracks."""
        res = database.calculate_qwar_metrics(fives=10, fours=10, threes=0, skips=0, total_tracks=20, avg_rating=4.5)
        self.assertAlmostEqual(res["qwar_per_track"], res["legacy_qwar"] / 20.0, delta=0.02)

    def test_productivity_active_artist(self):
        """Active artist productivity spans from career start to date.today()."""
        start = date(2020, 1, 1)
        active_years, tpy = database.calculate_artist_productivity(
            start_date=start,
            status="Active",
            latest_release_date_str="2024-01-01",
            total_catalog_tracks=100
        )
        self.assertGreaterEqual(active_years, 4.0)
        self.assertGreater(tpy, 0.0)

    def test_productivity_disbanded_artist_freezes_at_last_release(self):
        """Disbanded artist active years freeze at final release date, not today."""
        start = date(2015, 1, 1)
        last_rel = "2020-01-01"
        active_years, tpy = database.calculate_artist_productivity(
            start_date=start,
            status="Disbanded",
            latest_release_date_str=last_rel,
            total_catalog_tracks=50
        )
        # 2015 to 2020 is approx 5.0 years (1826 days / 365.25 = 5.0)
        self.assertAlmostEqual(active_years, 5.0, delta=0.1)
        self.assertAlmostEqual(tpy, 10.0, delta=0.5)

    def test_round_half_up_matches_sqlite(self):
        """_round_half_up must round .005 and .125 away from zero to match SQLite's ROUND()."""
        # In Python banker's rounding, round(3.125, 2) is 3.12 (rounds to even).
        # In SQLite ROUND(25.0 / 8, 2), 3.125 is 3.13.
        self.assertEqual(database._round_half_up(25.0 / 8, 2), 3.13)
        self.assertEqual(database._round_half_up(3.125, 2), 3.13)
        self.assertEqual(database._round_half_up(3.124, 2), 3.12)
        self.assertEqual(database._round_half_up(None, 2), None)

    def test_summary_metrics_parity_with_dashboard(self):
        """Verify get_artist_summary_metrics yields identical metrics to get_all_artists_summary."""
        with database.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM artists WHERE name = 'ParityBand'")
            cur.execute("INSERT INTO artists (name, artist_type, status) VALUES ('ParityBand', 'group', 'Active')")
            aid = cur.lastrowid
            cur.execute("""
                INSERT INTO albums (artist_id, title, release_date, album_type)
                VALUES (?, 'EP 1', '2024-01-01', 'ep')
            """, (aid,))
            alid = cur.lastrowid
            # 8 tracks with sum = 25.0 -> avg = 3.125
            ratings = [5.0, 4.0, 4.0, 3.0, 3.0, 2.0, 2.0, 2.0]
            for i, r in enumerate(ratings):
                cur.execute("""
                    INSERT INTO tracks (album_id, title, track_number, rating, track_type)
                    VALUES (?, ?, ?, ?, 'core')
                """, (alid, f"Track {i+1}", i + 1, r))

        deep_dive = database.get_artist_summary_metrics("ParityBand")
        all_artists = database.get_all_artists_summary()
        matched_summary = next(a for a in all_artists if a["artist"] == "ParityBand")

        self.assertIsNotNone(deep_dive)
        self.assertEqual(deep_dive["overall_avg"], 3.13)
        self.assertEqual(matched_summary["overall_avg"], 3.13)
        self.assertEqual(deep_dive["compacted_qwar"], matched_summary["compacted_qwar"])
        self.assertEqual(deep_dive["legacy_qwar"], matched_summary["legacy_qwar"])
        self.assertEqual(deep_dive["qwar_per_track"], matched_summary["qwar_per_track"])
        self.assertEqual(deep_dive["plotted_albums_count"], matched_summary["plotted_albums_count"])
        self.assertEqual(deep_dive["active_years"], matched_summary["active_years"])
        self.assertEqual(deep_dive["tracks_per_year"], matched_summary["tracks_per_year"])
        self.assertEqual(deep_dive["raw_total_albums"], matched_summary["raw_total_albums"])
        self.assertEqual(deep_dive["raw_total_tracks"], 8)
        self.assertEqual(deep_dive["plotted_albums_count"], 1)

    def test_productivity_disbanded_artist_fallback_without_last_release(self):
        """Disbanded artist without a valid latest release date falls back to today without error."""
        start = date(2020, 1, 1)
        active_years, tpy = database.calculate_artist_productivity(
            start_date=start,
            status="Disbanded",
            latest_release_date_str=None,
            total_catalog_tracks=20
        )
        self.assertGreaterEqual(active_years, 4.0)

    def test_productivity_zero_tracks_and_same_day_debut(self):
        """Rookie debut today with 0 tracks must return active_years=0.1, tpy=0.0 without divide-by-zero."""
        today = date.today()
        active_years, tpy = database.calculate_artist_productivity(
            start_date=today,
            status="Active",
            latest_release_date_str=str(today),
            total_catalog_tracks=0
        )
        self.assertEqual(active_years, 0.1)
        self.assertEqual(tpy, 0.0)

    def test_productivity_none_start_date(self):
        """None start_date safely yields (0.0, 0.0)."""
        active_years, tpy = database.calculate_artist_productivity(
            start_date=None,
            status="Active",
            latest_release_date_str="2024-01-01",
            total_catalog_tracks=10
        )
        self.assertEqual(active_years, 0.0)
        self.assertEqual(tpy, 0.0)

    def test_dashboard_summary_includes_title_bside_and_lp_metrics(self):
        """Backlog #3: get_all_artists_summary must return title/b-side splits and LP summaries matching Deep Dive."""
        with database.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM artists WHERE name = 'LeaderGroup'")
            cur.execute("INSERT INTO artists (name, artist_type, debut_year) VALUES ('LeaderGroup', 'group', 2022)")
            grp_id = cur.lastrowid
            cur.execute("INSERT INTO albums (artist_id, title, album_type, release_date) VALUES (?, 'First LP', 'LP', '2024-05-01')", (grp_id,))
            alb_id = cur.lastrowid
            cur.execute("INSERT INTO tracks (album_id, title, track_number, track_type, rating, is_title_track) VALUES (?, 'Title 1', 1, 'core', 5.0, 1)", (alb_id,))
            cur.execute("INSERT INTO tracks (album_id, title, track_number, track_type, rating, is_title_track) VALUES (?, 'B-Side 1', 2, 'core', 4.0, 0)", (alb_id,))
            cur.execute("INSERT INTO tracks (album_id, title, track_number, track_type, rating, is_title_track) VALUES (?, 'B-Side 2', 3, 'core', 3.0, 0)", (alb_id,))
            conn.commit()

        deep = database.get_artist_summary_metrics("LeaderGroup")
        dash = next(d for d in database.get_all_artists_summary() if d["artist"] == "LeaderGroup")

        self.assertEqual(dash["title_count"], deep["title_bsides"]["title_count"])
        self.assertEqual(dash["title_avg"], deep["title_bsides"]["title_avg"])
        self.assertEqual(dash["bside_count"], deep["title_bsides"]["bside_count"])
        self.assertEqual(dash["bside_avg"], deep["title_bsides"]["bside_avg"])
        self.assertEqual(len(dash["albums"]), 1)
        self.assertEqual(dash["albums"][0]["album_title"], "First LP")
        self.assertEqual(dash["albums"][0]["avg_score"], 4.0)
        self.assertEqual(dash["albums"][0]["rated_tracks"], 3)


if __name__ == "__main__":
    unittest.main()
