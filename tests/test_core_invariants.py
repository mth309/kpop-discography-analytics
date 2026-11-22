"""
Regression tests for core database schema invariants, canonical track deduplication,
watchlist graduation, and API model bounds.
"""

import os
import sqlite3
import tempfile
import unittest
from fastapi import HTTPException
from pydantic import ValidationError

import app
import database
import fetcher

TEST_DB_FILE = os.path.join(tempfile.gettempdir(), "test_core_invariants.db")


def setUpModule():
    for suffix in ["", "-wal", "-shm"]:
        p = TEST_DB_FILE + suffix
        if os.path.exists(p):
            os.remove(p)
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


class TestCoreInvariants(unittest.TestCase):
    """Verifies schema invariants, canonical deduplication, and model bounds."""

    def test_pydantic_track_rating_bounds(self):
        """TrackIn must enforce rating >= 1.0 and <= 5.0 to match SQLite CHECK constraint."""
        # Invalid: Below 1.0
        with self.assertRaises(ValidationError):
            app.TrackIn(track_number=1, title="Test Track", rating=0.5)

        with self.assertRaises(ValidationError):
            app.TrackIn(track_number=1, title="Test Track", rating=0.0)

        # Invalid: Above 5.0
        with self.assertRaises(ValidationError):
            app.TrackIn(track_number=1, title="Test Track", rating=5.5)

        # Valid: 1.0 to 5.0 and None (unrated)
        t1 = app.TrackIn(track_number=1, title="Valid 1", rating=1.0)
        self.assertEqual(t1.rating, 1.0)

        t1_5 = app.TrackIn(track_number=2, title="Valid 1.5", rating=1.5)
        self.assertEqual(t1_5.rating, 1.5)

        t5 = app.TrackIn(track_number=3, title="Valid 5", rating=5.0)
        self.assertEqual(t5.rating, 5.0)

        t_none = app.TrackIn(track_number=4, title="Unrated", rating=None)
        self.assertIsNone(t_none.rating)

    def test_track_type_default_is_core(self):
        """Default track_type must be 'core' and legacy 'group' must be migrated."""
        database.init_db()
        with database.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM tracks WHERE track_type = 'group'")
            count = cur.fetchone()[0]
            self.assertEqual(count, 0, f"Found {count} tracks with legacy track_type 'group'")

    def test_search_release_signature_and_param_support(self):
        """_make_request must accept params and search_release must return Optional[Dict]."""
        import inspect
        sig = inspect.signature(fetcher._make_request)
        self.assertIn("params", sig.parameters, "_make_request should support a 'params' parameter")

        sig_sr = inspect.signature(fetcher.search_release)
        self.assertNotEqual(sig_sr.return_annotation, list, "search_release should not annotate List return")

    def test_canonical_deduplication_without_advance_singles(self):
        """Single artist deep dive metrics must apply CANONICAL_UNIQUE_TRACK_SQL even with no advance singles."""
        database.init_db()
        with database.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM artists WHERE name LIKE '__Test%'")
            
            # Create an artist with NO advance singles (e.g. 1 EP with 1 core track and 1 non-canon track)
            cur.execute("INSERT INTO artists (name, artist_type) VALUES ('__TestCanonArtist__', 'group')")
            artist_id = cur.lastrowid

            cur.execute("INSERT INTO albums (artist_id, title, release_date, album_type) VALUES (?, 'EP 1', '2024-01-01', 'EP')", (artist_id,))
            album_id = cur.lastrowid

            # 1 core track rated 4.0, 1 non-canon track rated 1.0
            cur.execute("INSERT INTO tracks (album_id, track_number, title, rating, track_type) VALUES (?, 1, 'Core Song', 4.0, 'core')", (album_id,))
            cur.execute("INSERT INTO tracks (album_id, track_number, title, rating, track_type) VALUES (?, 2, 'Core Song (Inst.)', 1.0, 'non_canon')", (album_id,))
            conn.commit()

        try:
            # When advance_album_ids is empty, canonical deduplication must exclude the 1.0 inst track from overall_avg
            metrics = database.get_artist_summary_metrics("__TestCanonArtist__")
            
            # If canonical deduplication works: total_rated_tracks = 1, overall_avg = 4.0
            # If bug was present: total_rated_tracks = 2, overall_avg = 2.5
            self.assertEqual(metrics["total_rated_tracks"], 1, "Non-canon track should be excluded from total_rated_tracks")
            self.assertEqual(metrics["overall_avg"], 4.0, "Non-canon track (1.0) should be excluded from overall_avg")
        finally:
            with database.get_connection() as conn:
                conn.execute("DELETE FROM artists WHERE name LIKE '__Test%'")
                conn.commit()

    def test_canonical_deduplication_curly_vs_straight_apostrophe(self):
        """Tracks differing only by curly (’) vs straight (') apostrophe must deduplicate as one canonical track."""
        database.init_db()
        with database.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM artists WHERE name LIKE '__TestQuote%'")
            cur.execute("INSERT INTO artists (name, artist_type) VALUES ('__TestQuoteArtist__', 'group')")
            artist_id = cur.lastrowid

            cur.execute("INSERT INTO albums (artist_id, title, release_date, album_type) VALUES (?, 'Single 1', '2024-01-01', 'Single')", (artist_id,))
            s_id = cur.lastrowid
            cur.execute("INSERT INTO albums (artist_id, title, release_date, album_type) VALUES (?, 'EP 1', '2024-02-01', 'EP')", (artist_id,))
            ep_id = cur.lastrowid

            cur.execute("INSERT INTO tracks (album_id, track_number, title, rating, track_type) VALUES (?, 1, ?, 2.0, 'core')", (s_id, "Don\u2019t be happy"))
            cur.execute("INSERT INTO tracks (album_id, track_number, title, rating, track_type) VALUES (?, 1, ?, 2.0, 'core')", (ep_id, "Don't Be Happy"))
            conn.commit()

        try:
            metrics = database.get_artist_summary_metrics("__TestQuoteArtist__")
            self.assertEqual(metrics["total_rated_tracks"], 1)
            _, ratings_map = database.get_existing_track_defaults("__TestQuoteArtist__")
            self.assertIn("don't be happy", ratings_map)
            self.assertEqual(fetcher.normalize_title_quotes("Don\u2019t be happy"), "Don't be happy")
        finally:
            with database.get_connection() as conn:
                conn.execute("DELETE FROM artists WHERE name LIKE '__TestQuote%'")
                conn.commit()

    def test_canonical_deduplication_rated_priority_and_localized_b_sides(self):
        """Bug 3.3 Regression: Rated tracks must never be shadowed by unrated duplicates,
        and unique original B-sides on localized albums (e.g. Queen of Hearts) must be preserved.
        """
        database.init_db()
        with database.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM artists WHERE name LIKE '__TestCanonArtist2__'")

            cur.execute("INSERT INTO artists (name, artist_type) VALUES ('__TestCanonArtist2__', 'group')")
            artist_id = cur.lastrowid

            # 1. Single with a rated lead track (4.5) and a rated b-side (3.5)
            cur.execute("INSERT INTO albums (artist_id, title, release_date, album_type) VALUES (?, 'Debut Single', '2023-01-01', 'Single')", (artist_id,))
            single_id = cur.lastrowid
            cur.execute("INSERT INTO tracks (album_id, track_number, title, rating, track_type) VALUES (?, 1, 'Lead Song', 4.5, 'core')", (single_id,))
            cur.execute("INSERT INTO tracks (album_id, track_number, title, rating, track_type) VALUES (?, 2, 'Single B-Side', 3.5, 'core')", (single_id,))

            # 2. Later LP (8 tracks) containing 'Lead Song' unrated (rating = NULL)
            cur.execute("INSERT INTO albums (artist_id, title, release_date, album_type) VALUES (?, 'The 1st Album', '2023-09-01', 'LP')", (artist_id,))
            lp_id = cur.lastrowid
            cur.execute("INSERT INTO tracks (album_id, track_number, title, rating, track_type) VALUES (?, 1, 'Lead Song', NULL, 'core')", (lp_id,))
            for i in range(2, 9):
                cur.execute("INSERT INTO tracks (album_id, track_number, title, rating, track_type) VALUES (?, ?, ?, NULL, 'core')", (lp_id, i, f"LP Track {i}"))

            # 3. Localized Japanese edition containing:
            #    - 'Lead Song' (duplicate core)
            #    - 'Queen of Hearts' (original unique Japanese B-side rated 5.0)
            #    - 'Lead Song (JP ver.)' (alt_lang)
            #    - 'Lead Song (Inst.)' (non_canon)
            cur.execute("INSERT INTO albums (artist_id, title, release_date, album_type) VALUES (?, 'Lead Song -Japanese ver.-', '2023-11-01', 'Single')", (artist_id,))
            jp_album_id = cur.lastrowid
            cur.execute("INSERT INTO tracks (album_id, track_number, title, rating, track_type) VALUES (?, 1, 'Lead Song', NULL, 'core')", (jp_album_id,))
            cur.execute("INSERT INTO tracks (album_id, track_number, title, rating, track_type) VALUES (?, 2, 'Queen of Hearts', 5.0, 'core')", (jp_album_id,))
            cur.execute("INSERT INTO tracks (album_id, track_number, title, rating, track_type) VALUES (?, 3, 'Lead Song (JP ver.)', NULL, 'alt_lang')", (jp_album_id,))
            cur.execute("INSERT INTO tracks (album_id, track_number, title, rating, track_type) VALUES (?, 4, 'Lead Song (Inst.)', NULL, 'non_canon')", (jp_album_id,))

            conn.commit()

        try:
            metrics = database.get_artist_summary_metrics("__TestCanonArtist2__")

            # Part 1 Verification:
            # - 'Lead Song' was rated 4.5 on the single, but unrated on the 8-track LP.
            # - Under old buggy behavior: LP (8 tracks) shadowed Single (2 tracks), dropping Lead Song's rating!
            # - Under fixed behavior: The rated Single copy is protected and retained.
            #
            # Part 2 Verification:
            # - 'Queen of Hearts' is a unique B-side on a localized edition (-Japanese ver.-).
            # - It must NOT be excluded by blanket album title matching; its 5.0 rating must count.
            #
            # Total canonical rated tracks:
            # 1. 'Lead Song' (4.5 from Single)
            # 2. 'Single B-Side' (3.5 from Single)
            # 3. 'Queen of Hearts' (5.0 from Japanese single)
            # Total = 3 tracks, Avg = (4.5 + 3.5 + 5.0) / 3 = 4.33
            self.assertEqual(metrics["total_rated_tracks"], 3)
            self.assertEqual(metrics["overall_avg"], 4.33)
            self.assertEqual(metrics["overall_perfect"], 1)  # Queen of Hearts is 5.0

            # Verify that query for canonical tracks explicitly includes Queen of Hearts
            with database.get_connection() as conn:
                cur = conn.cursor()
                cur.execute(f"""
                    SELECT tr.title, tr.rating, al.title as album_title
                    FROM tracks tr
                    JOIN albums al ON tr.album_id = al.id
                    JOIN artists ar ON al.artist_id = ar.id
                    WHERE ar.name = '__TestCanonArtist2__'
                      AND tr.rating IS NOT NULL
                      AND {database.CANONICAL_UNIQUE_TRACK_SQL}
                    ORDER BY tr.title ASC
                """)
                rows = [dict(r) for r in cur.fetchall()]
                track_names = [r["title"] for r in rows]
                self.assertIn("Queen of Hearts", track_names)
                self.assertIn("Lead Song", track_names)
                self.assertIn("Single B-Side", track_names)

                # Ensure Lead Song is credited to the rated release (Debut Single)
                lead_row = next(r for r in rows if r["title"] == "Lead Song")
                self.assertEqual(lead_row["album_title"], "Debut Single")
                self.assertEqual(lead_row["rating"], 4.5)

        finally:
            with database.get_connection() as conn:
                conn.execute("DELETE FROM artists WHERE name LIKE '__TestCanonArtist2__'")
                conn.commit()

    def test_alt_lang_excluded_from_canonical_and_bsides(self):
        """Path 1: alt_lang and non_canon tracks are treated identically:
        both are excluded from canonical catalog counts, rating averages,
        score distribution, and B-side counts even if rated.
        Original substantive core tracks remain fully canonical.
        """
        database.init_db()
        with database.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM artists WHERE name LIKE '__TestAltLangArtist__'")

            cur.execute("INSERT INTO artists (name, artist_type) VALUES ('__TestAltLangArtist__', 'group')")
            artist_id = cur.lastrowid

            cur.execute("INSERT INTO albums (artist_id, title, release_date, album_type) VALUES (?, 'Japanese Special EP', '2024-01-01', 'EP')", (artist_id,))
            album_id = cur.lastrowid

            # 1. Original Title Track (core, rated 4.0)
            cur.execute("INSERT INTO tracks (album_id, track_number, title, rating, is_title_track, track_type) VALUES (?, 1, 'Original Hit', 4.0, 1, 'core')", (album_id,))
            # 2. Substantive B-Side (core, rated 4.0)
            cur.execute("INSERT INTO tracks (album_id, track_number, title, rating, is_title_track, track_type) VALUES (?, 2, 'Substantive B-Side', 4.0, 0, 'core')", (album_id,))
            # 3. Translated Alternate Language Track (alt_lang, rated 5.0) -> must be excluded from canonical scores
            cur.execute("INSERT INTO tracks (album_id, track_number, title, rating, is_title_track, track_type) VALUES (?, 3, 'Original Hit (JP ver.)', 5.0, 0, 'alt_lang')", (album_id,))
            # 4. Instrumental Offshoot Track (non_canon, rated 1.0) -> must be excluded from canonical scores
            cur.execute("INSERT INTO tracks (album_id, track_number, title, rating, is_title_track, track_type) VALUES (?, 4, 'Original Hit (Inst.)', 1.0, 0, 'non_canon')", (album_id,))

            conn.commit()

        try:
            metrics = database.get_artist_summary_metrics("__TestAltLangArtist__")

            # 1. Canonical track counts: only the 2 core tracks are canonical
            self.assertEqual(metrics["total_catalog_tracks"], 2)
            self.assertEqual(metrics["total_rated_tracks"], 2)

            # 2. Career score: exactly 4.0 ((4.0 + 4.0) / 2). Neither 5.0 (alt_lang) nor 1.0 (non_canon) pollutes it.
            self.assertEqual(metrics["overall_avg"], 4.0)
            self.assertEqual(metrics["overall_perfect"], 0, "Rated alt_lang 5.0 must not enter canonical perfect 5 count")

            # 3. Title Tracks vs B-Sides: exactly 1 title track and 1 B-side
            tb = metrics["title_bsides"]
            self.assertEqual(tb["title_count"], 1)
            self.assertEqual(tb["bside_count"], 1, "alt_lang track must not count as a B-side")
            self.assertEqual(tb["bside_avg"], 4.0)

            # 4. Direct SQL evaluation under CANONICAL_UNIQUE_TRACK_SQL
            with database.get_connection() as conn:
                cur = conn.cursor()
                cur.execute(f"""
                    SELECT tr.title, tr.track_type
                    FROM tracks tr
                    JOIN albums al ON tr.album_id = al.id
                    JOIN artists ar ON al.artist_id = ar.id
                    WHERE ar.name = '__TestAltLangArtist__'
                      AND {database.CANONICAL_UNIQUE_TRACK_SQL}
                """)
                canonical_titles = [r["title"] for r in cur.fetchall()]
                self.assertIn("Original Hit", canonical_titles)
                self.assertIn("Substantive B-Side", canonical_titles)
                self.assertNotIn("Original Hit (JP ver.)", canonical_titles)
                self.assertNotIn("Original Hit (Inst.)", canonical_titles)

        finally:
            with database.get_connection() as conn:
                conn.execute("DELETE FROM artists WHERE name LIKE '__TestAltLangArtist__'")
                conn.commit()

    def test_canonical_deduplication_prerelease_120d_window(self):
        """Bug 3.4 Regression (Option B):
        - A single released <= 120 days before an EP/LP is treated as a pre-release single;
          the later EP/LP copy shadows the single.
        - A single released > 120 days before an EP/LP is treated as a standalone era milestone;
          the earlier single shadows the later EP/LP copy.
        - Total unique canonical tracks are strictly preserved with zero duplication.
        """
        database.init_db()
        with database.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM artists WHERE name LIKE '__TestCanonArtist3__'")

            cur.execute("INSERT INTO artists (name, artist_type) VALUES ('__TestCanonArtist3__', 'group')")
            artist_id = cur.lastrowid

            # 1. Pre-release single: released ~62 days before LP
            cur.execute("INSERT INTO albums (artist_id, title, release_date, album_type) VALUES (?, 'Pre-release Single', '2023-07-01', 'Single')", (artist_id,))
            single_pre_id = cur.lastrowid
            cur.execute("INSERT INTO tracks (album_id, track_number, title, rating, track_type) VALUES (?, 1, 'Lead Single Track', 4.0, 'core')", (single_pre_id,))

            # 2. Standalone milestone single: released ~243 days before LP (> 120 days)
            cur.execute("INSERT INTO albums (artist_id, title, release_date, album_type) VALUES (?, 'Standalone Single', '2023-01-01', 'Single')", (artist_id,))
            single_stand_id = cur.lastrowid
            cur.execute("INSERT INTO tracks (album_id, track_number, title, rating, track_type) VALUES (?, 1, 'Iconic Standalone Track', 5.0, 'core')", (single_stand_id,))

            # 3. Full Album (LP): released 2023-09-01
            cur.execute("INSERT INTO albums (artist_id, title, release_date, album_type) VALUES (?, 'Full Album', '2023-09-01', 'LP')", (artist_id,))
            lp_id = cur.lastrowid
            cur.execute("INSERT INTO tracks (album_id, track_number, title, rating, track_type) VALUES (?, 1, 'Iconic Standalone Track', 5.0, 'core')", (lp_id,))
            cur.execute("INSERT INTO tracks (album_id, track_number, title, rating, track_type) VALUES (?, 2, 'Lead Single Track', 4.0, 'core')", (lp_id,))
            cur.execute("INSERT INTO tracks (album_id, track_number, title, rating, track_type) VALUES (?, 3, 'Album Deep Cut', 3.0, 'core')", (lp_id,))

            conn.commit()

        try:
            metrics = database.get_artist_summary_metrics("__TestCanonArtist3__")

            # 1. Total canonical rated tracks must be exactly 3 (no duplication)
            # 5.0 (Iconic) + 4.0 (Lead) + 3.0 (Deep Cut) = 12.0 / 3 = 4.0 avg
            self.assertEqual(metrics["total_rated_tracks"], 3)
            self.assertEqual(metrics["overall_avg"], 4.0)

            # 2. Advance / duplicate single detection:
            # - Pre-release Single (62d before LP) must be recognized as an advance single
            # - Standalone Single (243d before LP) must NOT be categorized as advance
            advance_album_ids = [a["album_id"] for a in metrics["albums"] if a["is_advance_single"]]
            self.assertIn(single_pre_id, advance_album_ids)
            self.assertNotIn(single_stand_id, advance_album_ids)

            # 3. Canonical track allocation verification:
            with database.get_connection() as conn:
                cur = conn.cursor()
                cur.execute(f"""
                    SELECT tr.title, al.title as album_title
                    FROM tracks tr
                    JOIN albums al ON tr.album_id = al.id
                    JOIN artists ar ON al.artist_id = ar.id
                    WHERE ar.name = '__TestCanonArtist3__'
                      AND {database.CANONICAL_UNIQUE_TRACK_SQL}
                    ORDER BY tr.title ASC
                """)
                canonical_rows = {r["title"]: r["album_title"] for r in cur.fetchall()}

                # Lead Single Track (<=120d) is shadowed by LP -> belongs to 'Full Album'
                self.assertEqual(canonical_rows.get("Lead Single Track"), "Full Album")

                # Iconic Standalone Track (>120d) shadows LP -> belongs to 'Standalone Single'
                self.assertEqual(canonical_rows.get("Iconic Standalone Track"), "Standalone Single")

                # Album Deep Cut is unique to LP -> belongs to 'Full Album'
                self.assertEqual(canonical_rows.get("Album Deep Cut"), "Full Album")

        finally:
            with database.get_connection() as conn:
                conn.execute("DELETE FROM artists WHERE name LIKE '__TestCanonArtist3__'")
                conn.commit()

    def test_watchlist_graduation_status_and_type_preservation(self):
        """Graduating a watchlist artist must carry over status, generation, and artist_type."""
        database.init_db()
        with database.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SAVEPOINT test_wl_grad")
            try:
                # 1. Add test watchlist artist who is 'Disbanded' and 'solo'
                test_artist_name = "__TestGraduationArtist__"
                cur.execute("""
                    INSERT INTO artist_watchlist (name, artist_type, generation, status, parent_groups)
                    VALUES (?, 'solo', '3rd Gen', 'Disbanded', 'IZ*ONE')
                """, (test_artist_name,))

                # 2. Simulate commit_album_payload creating the artist first with table defaults
                artist_id = database.get_or_create_artist(conn, test_artist_name)
                cur.execute("SELECT status, artist_type FROM artists WHERE id = ?", (artist_id,))
                initial = dict(cur.fetchone())
                self.assertEqual(initial["status"], "Active")
                self.assertEqual(initial["artist_type"], "group")

                # 3. Call graduate_watchlist_artist
                graduated = database.graduate_watchlist_artist(conn, test_artist_name)
                self.assertTrue(graduated)

                # 4. Verify status, generation, and artist_type were properly updated to the watchlist values
                cur.execute("SELECT generation, status, artist_type FROM artists WHERE id = ?", (artist_id,))
                updated = dict(cur.fetchone())
                self.assertEqual(updated["generation"], "3rd Gen")
                self.assertEqual(updated["status"], "Disbanded")
                self.assertEqual(updated["artist_type"], "solo")

            finally:
                cur.execute("ROLLBACK TO test_wl_grad")

    def test_csv_export_multi_group_soloist_no_duplication(self):
        """Multi-group soloists must not duplicate tracks in get_csv_export_data."""
        database.init_db()
        with database.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM artists WHERE name LIKE '__Test%'")
            
            # Create a test soloist with two parent groups
            cur.execute("INSERT INTO artists (name, artist_type) VALUES ('__TestSoloist__', 'solo')")
            solo_id = cur.lastrowid

            cur.execute("INSERT INTO artists (name, artist_type) VALUES ('__TestGroupA__', 'group')")
            group_a_id = cur.lastrowid
            cur.execute("INSERT INTO artists (name, artist_type) VALUES ('__TestGroupB__', 'group')")
            group_b_id = cur.lastrowid

            cur.execute("INSERT INTO artist_members (group_id, member_name, solo_artist_id, is_former) VALUES (?, 'Member', ?, 1)", (group_a_id, solo_id))
            cur.execute("INSERT INTO artist_members (group_id, member_name, solo_artist_id, is_former) VALUES (?, 'Member', ?, 0)", (group_b_id, solo_id))

            # Add 1 album and 1 track
            cur.execute("INSERT INTO albums (artist_id, title, release_date) VALUES (?, 'Solo Album', '2024-01-01')", (solo_id,))
            album_id = cur.lastrowid
            cur.execute("INSERT INTO tracks (album_id, track_number, title, rating, track_type) VALUES (?, 1, 'Solo Track', 4.0, 'core')", (album_id,))
            conn.commit()

        try:
            # Call get_csv_export_data
            roster_map, rows = database.get_csv_export_data()
            solo_rows = [r for r in rows if r["artist"] == "__TestSoloist__"]
            self.assertEqual(len(solo_rows), 1, f"Expected exactly 1 track row for soloist, got {len(solo_rows)}")
            self.assertIn("Ex-__TestGroupA__", solo_rows[0]["parent_group_name"])
            self.assertIn("__TestGroupB__", solo_rows[0]["parent_group_name"])
            self.assertEqual(solo_rows[0]["track_type"], "core")
        finally:
            with database.get_connection() as conn:
                conn.execute("DELETE FROM artists WHERE name LIKE '__Test%'")
                conn.commit()

    def test_get_all_artist_profiles_bulk_mapping(self):
        """get_all_artist_profiles must correctly map group members and soloist parent groups via bulk preloading."""
        database.init_db()
        with database.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM artists WHERE name LIKE '__Test%'")

            # 1. Create a group artist with 2 members (one with linked solo artist)
            cur.execute("INSERT INTO artists (name, artist_type, generation, status) VALUES ('__TestBulkGroup__', 'group', '4th Gen', 'Active')")
            group_id = cur.lastrowid

            cur.execute("INSERT INTO artists (name, artist_type, generation, status) VALUES ('__TestBulkSoloist__', 'solo', '4th Gen', 'Active')")
            solo_id = cur.lastrowid

            cur.execute("INSERT INTO artist_members (group_id, member_name, solo_artist_id, is_former) VALUES (?, 'Member 1', ?, 0)", (group_id, solo_id))
            cur.execute("INSERT INTO artist_members (group_id, member_name, solo_artist_id, is_former) VALUES (?, 'Member 2', NULL, 1)", (group_id,))
            conn.commit()

        try:
            profiles = database.get_all_artist_profiles()
            group_prof = next((p for p in profiles if p["name"] == "__TestBulkGroup__"), None)
            solo_prof = next((p for p in profiles if p["name"] == "__TestBulkSoloist__"), None)

            self.assertIsNotNone(group_prof, "Group profile should be present")
            self.assertIsNotNone(solo_prof, "Soloist profile should be present")

            # Group assertions
            self.assertEqual(group_prof["artist_type"], "group")
            self.assertIn("Member 1", group_prof["members"])
            self.assertIn("Member 2", group_prof["members"])
            self.assertEqual(len(group_prof["member_objects"]), 2)

            # Soloist assertions
            self.assertEqual(solo_prof["artist_type"], "solo")
            self.assertEqual(solo_prof["members"], [])
            self.assertEqual(len(solo_prof["parent_groups"]), 1)
            self.assertEqual(solo_prof["parent_groups"][0]["group_name"], "__TestBulkGroup__")
            self.assertEqual(solo_prof["parent_groups"][0]["is_former"], 0)

        finally:
            with database.get_connection() as conn:
                conn.execute("DELETE FROM artists WHERE name LIKE '__Test%'")
                conn.commit()

    def test_create_artist_profile_and_post_api(self):
        """Verifies direct artist profile creation, roster syncing, soloist affiliation, and API endpoint."""
        try:
            # 1. Group creation with roster (including former member asterisk)
            group_payload = {
                "name": "__TestNewGroup__",
                "artist_type": "group",
                "generation": "Gen 4",
                "status": "Active",
                "debut_year": 2021,
                "members": ["Member Alpha", "*Member Beta"],
            }
            group_id = database.create_artist_profile(group_payload)
            self.assertIsInstance(group_id, int)

            # Verify in DB
            with database.get_connection() as conn:
                cur = conn.cursor()
                row = cur.execute("SELECT name, artist_type, generation, status, debut_year FROM artists WHERE id = ?", (group_id,)).fetchone()
                self.assertEqual(row[0], "__TestNewGroup__")
                self.assertEqual(row[1], "group")
                self.assertEqual(row[2], "Gen 4")
                self.assertEqual(row[3], "Active")
                self.assertEqual(row[4], 2021)

                # Verify member roster
                members = cur.execute("SELECT member_name, is_former FROM artist_members WHERE group_id = ? ORDER BY member_name", (group_id,)).fetchall()
                self.assertEqual(len(members), 2)
                self.assertEqual(tuple(members[0]), ("Member Alpha", 0))
                self.assertEqual(tuple(members[1]), ("Member Beta", 1))

            # 2. Soloist creation with parent group affiliation and watchlist graduation
            # Put soloist on watchlist first
            with database.get_connection() as conn:
                conn.execute("INSERT INTO artist_watchlist (name, status) VALUES ('__TestNewSoloist__', 'Active')")
                conn.commit()

            solo_payload = {
                "name": "__TestNewSoloist__",
                "artist_type": "solo",
                "generation": "Gen 4",
                "status": "Active",
                "debut_year": 2022,
                "parent_groups": "__TestNewGroup__",
            }
            solo_id = database.create_artist_profile(solo_payload)
            self.assertIsInstance(solo_id, int)

            # Verify watchlist graduation (deleted from artist_watchlist)
            with database.get_connection() as conn:
                cur = conn.cursor()
                wl_count = cur.execute("SELECT COUNT(*) FROM artist_watchlist WHERE name = '__TestNewSoloist__'").fetchone()[0]
                self.assertEqual(wl_count, 0, "Artist should be graduated / removed from watchlist upon creation")

                # Verify soloist parent group link
                link = cur.execute(
                    "SELECT group_id, member_name FROM artist_members WHERE solo_artist_id = ?", (solo_id,)
                ).fetchone()
                self.assertIsNotNone(link, "Soloist should be linked to parent group in artist_members")
                self.assertEqual(link[0], group_id)
                self.assertEqual(link[1], "__TestNewSoloist__")

            # 3. Duplicate name prevention in database
            with self.assertRaises(sqlite3.IntegrityError):
                database.create_artist_profile(group_payload)

            # 4. FastAPI POST /api/artists endpoint verification
            api_payload = app.ArtistUpdate(
                name="__TestApiArtist__",
                artist_type="group",
                generation="Gen 5",
                status="Active",
                debut_year=2024,
                members=["Rookie One", "Rookie Two"],
            )
            resp = app.create_artist(api_payload)
            self.assertEqual(resp["status"], "created")
            self.assertIn("id", resp)

            # 5. FastAPI duplicate name raises 400 HTTPException
            with self.assertRaises(HTTPException) as ctx:
                app.create_artist(api_payload)
            self.assertEqual(ctx.exception.status_code, 400)
            self.assertIn("already exists", ctx.exception.detail)

        finally:
            with database.get_connection() as conn:
                conn.execute("DELETE FROM artists WHERE name LIKE '__Test%'")
                conn.execute("DELETE FROM artist_watchlist WHERE name LIKE '__Test%'")
                conn.commit()

    def test_add_album_scoped_mb_release_group_id_no_cross_artist_collision(self):
        """add_album must scope mb_release_group_id lookup by artist_id to prevent cross-artist collision."""
        with database.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("INSERT INTO artists (name, artist_type) VALUES ('__CollabArtistA', 'group')")
            a_id = cur.lastrowid
            cur.execute("INSERT INTO artists (name, artist_type) VALUES ('__CollabArtistB', 'group')")
            b_id = cur.lastrowid
            conn.commit()

            try:
                shared_rg_id = "test-shared-rg-uuid-999"
                album_a = database.add_album(conn, a_id, "Joint Single", "2024-01-01", "Single", mb_release_group_id=shared_rg_id)
                album_b = database.add_album(conn, b_id, "Joint Single", "2024-01-01", "Single", mb_release_group_id=shared_rg_id)

                # Both artists must have distinct album IDs despite sharing the same MBID
                self.assertNotEqual(album_a, album_b)

                # Calling add_album again for Artist A must return Artist A's album, not Artist B's
                re_fetched_a = database.add_album(conn, a_id, "Joint Single", "2024-01-01", "Single", mb_release_group_id=shared_rg_id)
                self.assertEqual(re_fetched_a, album_a)

                # Calling add_album again for Artist B must return Artist B's album
                re_fetched_b = database.add_album(conn, b_id, "Joint Single", "2024-01-01", "Single", mb_release_group_id=shared_rg_id)
                self.assertEqual(re_fetched_b, album_b)
            finally:
                conn.execute("DELETE FROM albums WHERE artist_id IN (?, ?)", (a_id, b_id))
                conn.execute("DELETE FROM artists WHERE id IN (?, ?)", (a_id, b_id))
                conn.commit()

    def test_404_http_exception_preservation_not_masked_as_500(self):
        """app endpoints that raise 404 must not have them caught and re-raised as 500."""
        from unittest.mock import patch

        # 1. export_db when file is missing raises 404 (not 500)
        with patch("app.checkpoint_wal"), patch("os.path.exists", return_value=False):
            with self.assertRaises(HTTPException) as ctx:
                app.export_db()
            self.assertEqual(ctx.exception.status_code, 404)

        # 2. fetch_release when no release found raises 404 (not 500)
        with patch("app.fetch_album_metadata", return_value=None):
            with self.assertRaises(HTTPException) as ctx:
                app.fetch_release(artist="NonExistentArtist", album="NonExistentAlbum")
            self.assertEqual(ctx.exception.status_code, 404)

        # 3. api_fetch_rg when tracklist not resolved raises 404 (not 500)
        with patch("app.get_tracklist_for_release_group", return_value=None):
            with self.assertRaises(HTTPException) as ctx:
                app.api_fetch_rg(rg_id="non-existent-rg", artist="SomeArtist")
            self.assertEqual(ctx.exception.status_code, 404)

    def test_stc_watchlist_predict_and_sync_proxies(self):
        """Watchlist predict and STC sync endpoints persist results and handle offline errors."""
        import json
        import urllib.error
        from unittest.mock import patch

        wl_id = database.add_watchlist_artist({"name": "__TestSTCProspect__", "artist_type": "group"})
        try:
            mock_pred = {
                "matched_artist": "__TestSTCProspect__",
                "avg_score": 4.25,
                "hit_count": 7,
                "total_tracks": 10,
                "tracks": [{"title": "Hit Song", "blend": 4.5, "hit_prob": 0.85}],
            }
            with patch("app._call_stc_sidecar", return_value=mock_pred):
                res = app.api_predict_watchlist_artist(wl_id, top_n=10)
            self.assertAlmostEqual(res["predicted_score"], 4.25, places=2)
            self.assertEqual(res["predicted_hits"], 7)
            self.assertEqual(res["predicted_total"], 10)
            self.assertEqual(json.loads(res["predicted_tracks_json"])[0]["title"], "Hit Song")
            wl_rows = database.get_watchlist_artists()
            wl_match = next(r for r in wl_rows if r["id"] == wl_id)
            self.assertIsNotNone(wl_match["predicted_at"])

            with self.assertRaises(HTTPException) as ctx_404:
                app.api_predict_watchlist_artist(9999999, top_n=10)
            self.assertEqual(ctx_404.exception.status_code, 404)

            mock_sync_start = {"status": "started", "running": True}
            with patch("app._call_stc_sidecar", return_value=mock_sync_start):
                sync_res = app.api_sync_stc_embeddings()
            self.assertEqual(sync_res["status"], "started")
            self.assertTrue(sync_res["running"])

            mock_sync_status = {
                "running": False,
                "status": "ok",
                "result": {
                    "status": "ok",
                    "newly_embedded": 3,
                    "newly_windows": 3,
                    "total_cached": 1549,
                    "newly_unmatched": [],
                },
                "error": None,
            }
            with patch("app._call_stc_sidecar", return_value=mock_sync_status):
                status_res = app.api_stc_sync_status()
            self.assertFalse(status_res["running"])
            self.assertEqual(status_res["result"]["newly_embedded"], 3)

            with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("offline")):
                with self.assertRaises(HTTPException) as ctx_503:
                    app.api_sync_stc_embeddings()
                self.assertEqual(ctx_503.exception.status_code, 503)

            with patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")):
                with self.assertRaises(HTTPException) as ctx_504:
                    app.api_stc_sync_status()
                self.assertEqual(ctx_504.exception.status_code, 504)
        finally:
            database.delete_watchlist_artist(wl_id)


if __name__ == "__main__":
    unittest.main()
