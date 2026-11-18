"""
Regression tests for MusicBrainz release candidate scoring, date normalization helpers,
and database initialization.
"""

import os
import tempfile
import unittest
from datetime import date
from unittest.mock import patch

import database
import fetcher
import app

TEST_DB_FILE = os.path.join(tempfile.gettempdir(), "test_ingestion_and_helpers.db")


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


class TestIngestionAndHelpers(unittest.TestCase):
    """Verifies ingestion candidate selection, date parsing, and schema initialization."""

    def test_resolve_career_start_date(self):
        """resolve_career_start_date handles full, partial, and fallback dates."""
        # Full date YYYY-MM-DD
        d1 = database.resolve_career_start_date("2021-12-01", 2021)
        self.assertEqual(d1, date(2021, 12, 1))

        # Partial date YYYY-MM
        d2 = database.resolve_career_start_date("2021-12", None)
        self.assertEqual(d2, date(2021, 12, 1))

        # Year-only string YYYY
        d3 = database.resolve_career_start_date("2021", None)
        self.assertEqual(d3, date(2021, 1, 1))

        # Fallback to debut year integer
        d4 = database.resolve_career_start_date(None, 2022)
        self.assertEqual(d4, date(2022, 1, 1))

        # Both None
        d5 = database.resolve_career_start_date(None, None)
        self.assertIsNone(d5)

    def test_musicbrainz_release_candidate_scoring(self):
        """_pick_best_release prioritizes official status, full track count, and English/Latin international editions."""
        # Scenario 1: Official status beats bootlegs/promos, full track count beats samplers
        candidates = [
            {"id": "rel-us-sampler", "country": "US", "status": "Official", "track-count": 5},
            {"id": "rel-kr-bootleg", "country": "KR", "status": "Promotion", "track-count": 10},
            {"id": "rel-kr-official", "country": "KR", "status": "Official", "track-count": 10,
             "text-representation": {"script": "Kore", "language": "kor"}},
        ]
        best = fetcher._pick_best_release(candidates)
        self.assertIsNotNone(best)
        self.assertEqual(best["id"], "rel-kr-official")

        # Scenario 2: When full track count is tied, English/Latin international release beats domestic Hangul
        candidates_with_intl = [
            {"id": "rel-kr-hangul", "country": "KR", "status": "Official", "track-count": 10,
             "text-representation": {"script": "Kore", "language": "kor"}},
            {"id": "rel-xw-english", "country": "XW", "status": "Official", "track-count": 10,
             "text-representation": {"script": "Latn", "language": "eng"}},
        ]
        best_intl = fetcher._pick_best_release(candidates_with_intl)
        self.assertIsNotNone(best_intl)
        self.assertEqual(best_intl["id"], "rel-xw-english")

        # Scenario 3: When no international release exists, falls back cleanly to domestic Korean release
        candidates_kr_only = [
            {"id": "rel-kr-hangul-only", "country": "KR", "status": "Official", "track-count": 8,
             "text-representation": {"script": "Kore", "language": "kor"}},
        ]
        best_kr = fetcher._pick_best_release(candidates_kr_only)
        self.assertIsNotNone(best_kr)
        self.assertEqual(best_kr["id"], "rel-kr-hangul-only")

    def test_init_db_runs_cleanly_without_redundant_ddl(self):
        """init_db runs cleanly and leaves the schema consistent and migrated."""
        database.init_db()
        with database.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("PRAGMA user_version;")
            version = cur.fetchone()[0]
            self.assertGreaterEqual(version, 1)

            cur.execute("PRAGMA table_info(artist_watchlist)")
            cols = {row["name"] for row in cur.fetchall()}
            self.assertIn("generation", cols)
            self.assertIn("status", cols)
            self.assertIn("parent_groups", cols)
            self.assertNotIn("recommended_by", cols)
            self.assertNotIn("sample_track", cols)

            cur.execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'releases'")
            self.assertEqual(cur.fetchone()[0], 0)

            cur.execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'index' AND name = 'idx_albums_artist_title_type'")
            self.assertEqual(cur.fetchone()[0], 0)

            cur.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'tracks'")
            tracks_sql = cur.fetchone()[0]
            self.assertIn("DEFAULT 'core'", tracks_sql)

    def test_get_album_tracklist_excludes_video_media_and_no_auto_title(self):
        """get_album_tracklist must skip DVD/Blu-ray video discs and never auto-flag is_title_track."""
        mock_response = {
            "title": "Album With Bonus DVD",
            "date": "2023-05-01",
            "release-group": {"id": "rg-uuid-1", "primary-type": "Album"},
            "artist-credit": [{"name": "Test Group"}],
            "media": [
                {
                    "format": "CD",
                    "tracks": [
                        {"title": "Track 1"},
                        {"title": "Track 2"},
                    ]
                },
                {
                    "format": "DVD",
                    "tracks": [
                        {"title": "Track 1 (Music Video)"},
                        {"title": "Making Film"},
                    ]
                },
                {
                    "format": "Blu-ray",
                    "tracks": [
                        {"title": "Concert Live Footage"},
                    ]
                }
            ]
        }

        with patch("fetcher._make_request", return_value=mock_response):
            payload = fetcher.get_album_tracklist("mbid-test-123")
            self.assertEqual(payload["title"], "Album With Bonus DVD")
            self.assertEqual(payload["album_type"], "LP")
            self.assertEqual(len(payload["tracks"]), 2)
            self.assertEqual([t["title"] for t in payload["tracks"]], ["Track 1", "Track 2"])
            self.assertFalse(any(t["is_title_track"] for t in payload["tracks"]))

        # Single-track release must ALSO remain is_title_track = False (no auto-flagging)
        mock_single_response = {
            "title": "Solo Single",
            "date": "2023-06-01",
            "release-group": {"id": "rg-uuid-2", "primary-type": "Single"},
            "artist-credit": [{"name": "Test Group"}],
            "media": [
                {
                    "format": "Digital Media",
                    "tracks": [{"title": "Solo Single"}]
                }
            ]
        }
        with patch("fetcher._make_request", return_value=mock_single_response):
            single_payload = fetcher.get_album_tracklist("mbid-test-single")
            self.assertEqual(len(single_payload["tracks"]), 1)
            self.assertFalse(single_payload["tracks"][0]["is_title_track"], "1-track singles must not auto-flag title track")

    def test_get_artist_discography_filters_secondary_types(self):
        """get_artist_discography must exclude compilations, live albums, remixes, and mixtapes."""
        mock_rg_response = {
            "release-groups": [
                {
                    "id": "rg-core-lp",
                    "title": "First Full Album",
                    "first-release-date": "2023-01-01",
                    "primary-type": "Album",
                    "secondary-types": []
                },
                {
                    "id": "rg-compilation",
                    "title": "Best of Hits",
                    "first-release-date": "2023-03-01",
                    "primary-type": "Album",
                    "secondary-types": ["Compilation"]
                },
                {
                    "id": "rg-live",
                    "title": "World Tour Live",
                    "first-release-date": "2023-05-01",
                    "primary-type": "Album",
                    "secondary-types": ["Live"]
                },
                {
                    "id": "rg-remix",
                    "title": "Club Remix Package",
                    "first-release-date": "2023-07-01",
                    "primary-type": "EP",
                    "secondary-types": ["Remix"]
                },
                {
                    "id": "rg-mixtape",
                    "title": "Pre-debut Mixtape",
                    "first-release-date": "2022-12-01",
                    "primary-type": "Other",
                    "secondary-types": ["Mixtape/Street"]
                },
                {
                    "id": "rg-core-single",
                    "title": "Second Single",
                    "first-release-date": "2023-09-01",
                    "primary-type": "Single",
                    "secondary-types": []
                }
            ]
        }

        with patch("fetcher.search_artist_id", return_value="art-uuid-999"), \
             patch("fetcher._make_request", return_value=mock_rg_response):
            discog = fetcher.get_artist_discography("Test Group")
            titles = [d["title"] for d in discog]
            self.assertEqual(titles, ["First Full Album", "Second Single"])
            # Album primary type normalized to LP
            self.assertEqual(discog[0]["primary_type"], "LP")
            self.assertEqual(discog[1]["primary_type"], "Single")

    def test_staged_album_title_track_auto_enrichment(self):
        """Bug 4.2: enrich_staged_album must propagate prior title tracks to new albums
        while deliberately excluding alt_lang and non_canon versions.
        """
        artist_name = "__EnrichTestArtist__"
        database.init_db()
        with database.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM artists WHERE name = ?", (artist_name,))
            cur.execute("INSERT INTO artists (name, artist_type) VALUES (?, 'group')", (artist_name,))
            aid = cur.lastrowid

            # Seed a prior single with:
            # 1. Official title track ('Promoted Hit') -> is_title_track = 1, core
            # 2. Standard B-Side ('Deep Cut') -> is_title_track = 0, core
            # 3. Instrumental version ('Promoted Hit (Inst.)') -> is_title_track = 0, non_canon
            cur.execute("INSERT INTO albums (artist_id, title, release_date, album_type) VALUES (?, 'Prior Single', '2023-01-01', 'Single')", (aid,))
            alid = cur.lastrowid
            cur.execute("INSERT INTO tracks (album_id, track_number, title, rating, is_title_track, track_type) VALUES (?, 1, 'Promoted Hit', 4.5, 1, 'core')", (alid,))
            cur.execute("INSERT INTO tracks (album_id, track_number, title, rating, is_title_track, track_type) VALUES (?, 2, 'Deep Cut', 3.5, 0, 'core')", (alid,))
            cur.execute("INSERT INTO tracks (album_id, track_number, title, rating, is_title_track, track_type) VALUES (?, 3, 'Promoted Hit (Inst.)', 2.0, 0, 'non_canon')", (alid,))
            conn.commit()

        try:
            # Step 1: Verify get_existing_title_track_titles only captures 'promoted hit'
            prior_titles = database.get_existing_title_track_titles(artist_name)
            self.assertIn("promoted hit", prior_titles)
            self.assertNotIn("deep cut", prior_titles)
            self.assertNotIn("promoted hit (inst.)", prior_titles)

            prior_ratings = database.get_existing_track_ratings(artist_name)
            self.assertEqual(prior_ratings.get("promoted hit"), 4.5)
            self.assertEqual(prior_ratings.get("deep cut"), 3.5)
            self.assertNotIn("promoted hit (inst.)", prior_ratings)

            # Step 2: Simulate staging a later full LP containing:
            # - Track 1: 'New LP Title Track' (marked true in staging)
            # - Track 2: 'Promoted Hit' (core, re-released; marked false initially)
            # - Track 3: 'Deep Cut' (core b-side, re-released; marked false)
            # - Track 4: 'Promoted Hit' (alt_lang Japanese ver; marked false)
            # - Track 5: 'Promoted Hit' (non_canon remix; marked false)
            staged_payload = {
                "artist": artist_name,
                "title": "Later Studio Album",
                "release_date": "2023-09-01",
                "album_type": "LP",
                "tracks": [
                    {"track_number": 1, "title": "New LP Title Track", "track_type": "core", "is_title_track": True},
                    {"track_number": 2, "title": "Promoted Hit", "track_type": "core", "is_title_track": False},
                    {"track_number": 3, "title": "Deep Cut", "track_type": "core", "is_title_track": False},
                    {"track_number": 4, "title": "Promoted Hit", "track_type": "alt_lang", "is_title_track": False},
                    {"track_number": 5, "title": "Promoted Hit", "track_type": "non_canon", "is_title_track": False},
                ]
            }

            enriched = app.enrich_staged_album(staged_payload)
            tracks_by_num = {t["track_number"]: t for t in enriched["tracks"]}

            # 1. New LP Title Track retains its flag
            self.assertTrue(tracks_by_num[1]["is_title_track"])

            # 2. Re-released core version of 'Promoted Hit' is auto-enriched as a title track & rating
            self.assertTrue(tracks_by_num[2]["is_title_track"], "Core version of prior title track must be auto-enriched")
            self.assertEqual(tracks_by_num[2].get("rating"), 4.5, "Core version must inherit prior rating")

            # 3. 'Deep Cut' was not a prior title track, remains False, but inherits its prior rating
            self.assertFalse(tracks_by_num[3]["is_title_track"])
            self.assertEqual(tracks_by_num[3].get("rating"), 3.5, "Core B-side must inherit prior rating")

            # 4. alt_lang and non_canon versions of 'Promoted Hit' must NOT be auto-flagged or auto-rated
            self.assertFalse(tracks_by_num[4]["is_title_track"], "alt_lang tracks must not be auto-enriched as title tracks")
            self.assertIsNone(tracks_by_num[4].get("rating"), "alt_lang tracks must not inherit prior rating")
            self.assertFalse(tracks_by_num[5]["is_title_track"], "non_canon tracks must not be auto-enriched as title tracks")
            self.assertIsNone(tracks_by_num[5].get("rating"), "non_canon tracks must not inherit prior rating")

        finally:
            with database.get_connection() as conn:
                conn.execute("DELETE FROM artists WHERE name = ?", (artist_name,))
                conn.commit()


if __name__ == "__main__":
    unittest.main()
