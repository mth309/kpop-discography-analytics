import os
import tempfile
import unittest
import database

TEST_DB_FILE = os.path.join(tempfile.gettempdir(), "test_cumulative_qwar.db")

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

class TestCumulativeQWar(unittest.TestCase):

    def setUp(self):
        database.init_db()
        with database.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM artists WHERE name LIKE '__TestQWar%'")

    def tearDown(self):
        with database.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM artists WHERE name LIKE '__TestQWar%'")

    def test_cumulative_qwar_progression_and_zero_drift(self):
        """Phase 1: Cumulative Q-WAR points progress per release and exactly match overall summary metrics on final release."""
        with database.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("INSERT INTO artists (name, artist_type, debut_year) VALUES ('__TestQWarGroup__', 'group', 2022)")
            ar_id = cur.lastrowid

            # Release 1: Debut EP (6 tracks: two 5s, three 4s, one 3)
            cur.execute("INSERT INTO albums (artist_id, title, release_date, album_type) VALUES (?, 'EP 1', '2022-05-01', 'EP')", (ar_id,))
            al1_id = cur.lastrowid
            for i, r in enumerate([5.0, 5.0, 4.0, 4.0, 4.0, 3.0], 1):
                cur.execute("INSERT INTO tracks (album_id, track_number, title, rating, track_type) VALUES (?, ?, ?, ?, 'core')",
                            (al1_id, i, f"EP1_Track_{i}", r))

            # Release 2: Advance Single (1 track, 5.0) which reappears on EP 2
            cur.execute("INSERT INTO albums (artist_id, title, release_date, album_type) VALUES (?, 'Single 1', '2023-01-01', 'Single')", (ar_id,))
            al_s1_id = cur.lastrowid
            cur.execute("INSERT INTO tracks (album_id, track_number, title, rating, track_type) VALUES (?, 1, 'Advance_Song', 5.0, 'core')",
                        (al_s1_id,))

            # Release 3: 2nd EP (6 tracks: Advance_Song with 5.0, plus two 5s, two 4s, one 3)
            cur.execute("INSERT INTO albums (artist_id, title, release_date, album_type) VALUES (?, 'EP 2', '2023-03-01', 'EP')", (ar_id,))
            al2_id = cur.lastrowid
            cur.execute("INSERT INTO tracks (album_id, track_number, title, rating, track_type) VALUES (?, 1, 'Advance_Song', 5.0, 'core')",
                        (al2_id,))
            for i, r in enumerate([5.0, 5.0, 4.0, 4.0, 3.0], 2):
                cur.execute("INSERT INTO tracks (album_id, track_number, title, rating, track_type) VALUES (?, ?, ?, ?, 'core')",
                            (al2_id, i, f"EP2_Track_{i}", r))

        # Retrieve summary metrics for this test artist
        summary = database.get_artist_summary_metrics('__TestQWarGroup__')
        albums = summary["albums"]

        self.assertEqual(len(albums), 3)

        # Check album 1 (Debut EP)
        ep1 = albums[0]
        self.assertEqual(ep1["album_title"], "EP 1")
        self.assertEqual(ep1["cumulative_rated_tracks"], 6)
        self.assertGreater(ep1["cumulative_qwar"], 0.0)

        # Check album 2 (Advance Single)
        # Advance song is deduplicated to EP 2 (larger track count), so single adds 0 canonical tracks.
        single1 = albums[1]
        self.assertEqual(single1["album_title"], "Single 1")
        self.assertTrue(single1["is_advance_single"])
        self.assertEqual(single1["cumulative_rated_tracks"], 6, "Cumulative tracks should stay at 6 because single is non-canonical preview")
        self.assertEqual(single1["cumulative_qwar"], ep1["cumulative_qwar"], "Cumulative Q-WAR should hold steady")

        # Check album 3 (EP 2)
        ep2 = albums[2]
        self.assertEqual(ep2["album_title"], "EP 2")
        self.assertEqual(ep2["cumulative_rated_tracks"], 12)
        self.assertGreater(ep2["cumulative_qwar"], ep1["cumulative_qwar"])

        # INVARIANT CHECK: Final album cumulative metrics must EXACTLY equal overall artist metrics (ZERO DRIFT)
        self.assertEqual(ep2["cumulative_qwar"], summary["compacted_qwar"])
        self.assertEqual(ep2["cumulative_legacy_qwar"], summary["legacy_qwar"])
        self.assertEqual(ep2["cumulative_qwar_per_track"], summary["qwar_per_track"])
        self.assertEqual(ep2["cumulative_rated_tracks"], summary["total_rated_tracks"])
        self.assertEqual(ep2["cumulative_avg_score"], summary["overall_avg"])

    def test_compare_artists_cumulative_qwar_enrichment(self):
        """Phase 4 Backend: /api/analytics/compare delivers cumulative Q-WAR attributes across compared artists."""
        import app
        with database.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("INSERT INTO artists (name, artist_type, debut_year) VALUES ('__TestQWarGroupA__', 'group', 2021)")
            ar_a_id = cur.lastrowid
            cur.execute("INSERT INTO albums (artist_id, title, release_date, album_type) VALUES (?, 'A EP 1', '2021-06-01', 'EP')", (ar_a_id,))
            al_a_id = cur.lastrowid
            for i, r in enumerate([4.0, 4.0, 5.0, 3.0], 1):
                cur.execute("INSERT INTO tracks (album_id, track_number, title, rating, track_type) VALUES (?, ?, ?, ?, 'core')",
                            (al_a_id, i, f"A_Track_{i}", r))

            cur.execute("INSERT INTO artists (name, artist_type, debut_year) VALUES ('__TestQWarGroupB__', 'group', 2022)")
            ar_b_id = cur.lastrowid
            cur.execute("INSERT INTO albums (artist_id, title, release_date, album_type) VALUES (?, 'B EP 1', '2022-06-01', 'EP')", (ar_b_id,))
            al_b_id = cur.lastrowid
            for i, r in enumerate([5.0, 5.0, 5.0, 5.0], 1):
                cur.execute("INSERT INTO tracks (album_id, track_number, title, rating, track_type) VALUES (?, ?, ?, ?, 'core')",
                            (al_b_id, i, f"B_Track_{i}", r))

        results = app.compare_artists('__TestQWarGroupA__, __TestQWarGroupB__')
        self.assertEqual(len(results), 2)
        artist_a = results[0]
        artist_b = results[1]

        self.assertIn("cumulative_qwar", artist_a["albums"][0])
        self.assertIn("cumulative_qwar", artist_b["albums"][0])
        self.assertGreater(artist_b["albums"][0]["cumulative_qwar"], artist_a["albums"][0]["cumulative_qwar"])


if __name__ == "__main__":
    unittest.main()
