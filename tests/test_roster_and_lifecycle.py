import os
import tempfile
import unittest

import database

TEST_DB_FILE = os.path.join(tempfile.gettempdir(), "test_roster_and_lifecycle.db")

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


class TestRosterAndLifecycle(unittest.TestCase):
    """
    Backlog Item #9: Tests for member rosters, soloist affiliations, cascade safety,
    and album commit/update lifecycle.
    """

    def setUp(self):
        """Clean tables before each test to maintain state isolation."""
        with database.get_connection() as conn:
            conn.execute("DELETE FROM tracks")
            conn.execute("DELETE FROM albums")
            conn.execute("DELETE FROM artist_members")
            conn.execute("DELETE FROM artist_watchlist")
            conn.execute("DELETE FROM artists")
            conn.commit()

    def test_sync_artist_members_ordering_and_former_status(self):
        """Group roster preserves display order, former marker (*), and replaces on update."""
        with database.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("INSERT INTO artists (name, artist_type) VALUES ('NMIXX', 'group')")
            nmixx_id = cur.lastrowid

            # Initial roster with 1 former member (*Jinni)
            roster = ["Lily", "Haewon", "Sullyoon", "Bae", "Jiwoo", "Kyujin", "*Jinni"]
            database.sync_artist_members(conn, nmixx_id, roster)
            conn.commit()

            members = database.get_artist_members(conn, nmixx_id)
            self.assertEqual(len(members), 7)
            # Active members come first, ordered
            active_names = [m["member_name"] for m in members if m["is_former"] == 0]
            self.assertEqual(active_names, ["Lily", "Haewon", "Sullyoon", "Bae", "Jiwoo", "Kyujin"])
            
            # Former member
            former = [m for m in members if m["is_former"] == 1]
            self.assertEqual(len(former), 1)
            self.assertEqual(former[0]["member_name"], "Jinni")

            # Updating roster replaces members cleanly
            updated_roster = ["Lily", "Haewon", "Sullyoon", "Bae", "Jiwoo", "Kyujin"]
            database.sync_artist_members(conn, nmixx_id, updated_roster)
            conn.commit()

            members_after = database.get_artist_members(conn, nmixx_id)
            self.assertEqual(len(members_after), 6)
            self.assertFalse(any(m["member_name"] == "Jinni" for m in members_after))

    def test_sync_artist_members_auto_links_existing_soloist(self):
        """sync_artist_members automatically links member to an existing solo artist by name."""
        with database.get_connection() as conn:
            cur = conn.cursor()
            # Create a soloist first
            cur.execute("INSERT INTO artists (name, artist_type) VALUES ('Taeyeon', 'solo')")
            taeyeon_solo_id = cur.lastrowid

            # Create group
            cur.execute("INSERT INTO artists (name, artist_type) VALUES ('Girls Generation', 'group')")
            snsd_id = cur.lastrowid

            # Sync group members including Taeyeon
            roster = ["Taeyeon", "Sunny", "Tiffany", "Hyoyeon", "Yuri", "Sooyoung", "Yoona", "Seohyun", "*Jessica"]
            database.sync_artist_members(conn, snsd_id, roster)
            conn.commit()

            members = database.get_artist_members(conn, snsd_id)
            taeyeon_row = next(m for m in members if m["member_name"] == "Taeyeon")
            self.assertEqual(taeyeon_row["solo_artist_id"], taeyeon_solo_id)

    def test_sync_soloist_affiliations_two_way_mapping(self):
        """sync_soloist_affiliations links soloist to parent groups with former status."""
        with database.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("INSERT INTO artists (name, artist_type) VALUES ('Girls Generation', 'group')")
            cur.execute("INSERT INTO artists (name, artist_type) VALUES ('GOT the beat', 'group')")
            cur.execute("INSERT INTO artists (name, artist_type) VALUES ('Taeyeon', 'solo')")
            taeyeon_id = cur.lastrowid

            # Link Taeyeon to active SNSD and former GOT the beat
            database.sync_soloist_affiliations(conn, taeyeon_id, "Taeyeon", "Girls Generation, *GOT the beat")
            conn.commit()

            parents = database.get_parent_groups_for_soloist(conn, taeyeon_id)
            self.assertEqual(len(parents), 2)
            
            snsd_entry = next(p for p in parents if p["group_name"] == "Girls Generation")
            self.assertEqual(snsd_entry["is_former"], 0)

            got_entry = next(p for p in parents if p["group_name"] == "GOT the beat")
            self.assertEqual(got_entry["is_former"], 1)

            # Unlink by providing empty string
            database.sync_soloist_affiliations(conn, taeyeon_id, "Taeyeon", "")
            conn.commit()
            self.assertEqual(len(database.get_parent_groups_for_soloist(conn, taeyeon_id)), 0)

    def test_soloist_affiliations_mixed_and_unregistered_groups(self):
        """
        Handles soloist with unregistered groups (e.g. Wonder Girls not in DB),
        mixed active/former groups, and arbitrary input order without crashing.
        """
        with database.get_connection() as conn:
            cur = conn.cursor()
            # Only create 4MINUTE in the database; Wonder Girls and Trouble Maker do NOT exist yet
            cur.execute("INSERT INTO artists (name, artist_type, debut_year) VALUES ('4MINUTE', 'group', 2009)")
            four_minute_id = cur.lastrowid
            cur.execute("INSERT INTO artists (name, artist_type, debut_year) VALUES ('HyunA', 'solo', 2010)")
            hyuna_id = cur.lastrowid

            # 1. Input string containing unregistered groups (*Wonder Girls, *Trouble Maker) and 4MINUTE
            # In arbitrary order: former unregistered first, then active registered, then former unregistered
            input_str = "*Wonder Girls, 4MINUTE, *Trouble Maker"
            database.sync_soloist_affiliations(conn, hyuna_id, "HyunA", input_str)
            conn.commit()

            # Must not crash; 4MINUTE is linked while Wonder Girls & Trouble Maker persist as unlinked dummy entries
            parents = database.get_parent_groups_for_soloist(conn, hyuna_id)
            self.assertEqual(len(parents), 3)
            self.assertEqual(parents[0]["group_name"], "4MINUTE")
            self.assertEqual(parents[0]["group_id"], four_minute_id)
            self.assertEqual(parents[0]["is_former"], 0)
            self.assertEqual(
                [(p["group_name"], p["group_id"], bool(p["is_former"])) for p in parents[1:]],
                [("Wonder Girls", None, True), ("Trouble Maker", None, True)]
            )

            # 2. Later, Wonder Girls is imported/added to the database
            cur.execute("INSERT INTO artists (name, artist_type, debut_year) VALUES ('Wonder Girls', 'group', 2007)")
            conn.commit()

            # Re-syncing with Wonder Girls now present in the catalog
            # Reverse order input: 4MINUTE first, then *Wonder Girls
            database.sync_soloist_affiliations(conn, hyuna_id, "HyunA", "4MINUTE, *Wonder Girls")
            conn.commit()

            parents_updated = database.get_parent_groups_for_soloist(conn, hyuna_id)
            self.assertEqual(len(parents_updated), 2)

            # Active group (4MINUTE) comes first (is_former=0)
            self.assertEqual(parents_updated[0]["group_name"], "4MINUTE")
            self.assertEqual(parents_updated[0]["is_former"], 0)

            # Former group (*Wonder Girls) comes second (is_former=1)
            self.assertEqual(parents_updated[1]["group_name"], "Wonder Girls")
            self.assertEqual(parents_updated[1]["is_former"], 1)

    def test_commit_album_payload_create_and_update(self):
        """commit_album_payload correctly creates and subsequently updates an album without duplicating tracks."""
        # 1. Create brand new album
        create_payload = {
            "artist": "IVE",
            "title": "I'VE MINE",
            "release_date": "2023-10-13",
            "album_type": "EP",
            "tracks": [
                {"title": "Baddie", "rating": 5.0, "is_title_track": True, "track_type": "core", "track_number": 1},
                {"title": "Off The Record", "rating": 4.5, "is_title_track": True, "track_type": "core", "track_number": 2},
                {"title": "Either Way", "rating": 4.0, "is_title_track": True, "track_type": "core", "track_number": 3},
            ]
        }
        album_id = database.commit_album_payload(create_payload)
        self.assertIsNotNone(album_id)

        with database.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM tracks WHERE album_id = ?", (album_id,))
            self.assertEqual(cur.fetchone()[0], 3)

        # 2. Update existing album with 4 tracks (updating title and adding tracks)
        update_payload = {
            "album_id": album_id,
            "artist": "IVE",
            "title": "I'VE MINE (Special Edition)",
            "release_date": "2023-10-13",
            "album_type": "EP",
            "tracks": [
                {"title": "Baddie", "rating": 5.0, "is_title_track": True, "track_type": "core", "track_number": 1},
                {"title": "Off The Record", "rating": 4.5, "is_title_track": True, "track_type": "core", "track_number": 2},
                {"title": "Either Way", "rating": 4.0, "is_title_track": True, "track_type": "core", "track_number": 3},
                {"title": "Holy Moly", "rating": 3.5, "is_title_track": False, "track_type": "core", "track_number": 4},
            ]
        }
        returned_id = database.commit_album_payload(update_payload, album_id=album_id)
        self.assertEqual(returned_id, album_id)

        with database.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT title FROM albums WHERE id = ?", (album_id,))
            self.assertEqual(cur.fetchone()[0], "I'VE MINE (Special Edition)")

            # Must have exactly 4 tracks now (not 3 + 4 = 7)
            cur.execute("SELECT COUNT(*) FROM tracks WHERE album_id = ?", (album_id,))
            self.assertEqual(cur.fetchone()[0], 4)

    def test_delete_album_cascades_tracks(self):
        """delete_album cascades deletion of all associated tracks via foreign key."""
        payload = {
            "artist": "STAYC",
            "title": "TEENFRESH",
            "release_date": "2023-08-16",
            "album_type": "EP",
            "tracks": [
                {"title": "Bubble", "rating": 5.0, "is_title_track": True, "track_type": "core"},
                {"title": "Not Like You", "rating": 4.0, "is_title_track": False, "track_type": "core"},
            ]
        }
        album_id = database.commit_album_payload(payload)

        with database.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM tracks WHERE album_id = ?", (album_id,))
            self.assertEqual(cur.fetchone()[0], 2)

        # Delete album
        database.delete_album(album_id)

        with database.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM albums WHERE id = ?", (album_id,))
            self.assertEqual(cur.fetchone()[0], 0)
            cur.execute("SELECT COUNT(*) FROM tracks WHERE album_id = ?", (album_id,))
            self.assertEqual(cur.fetchone()[0], 0, "Tracks were not cascaded upon album deletion")

    def test_delete_artist_cascades_albums_and_members(self):
        """delete_artist cascades albums, tracks, and group roster rows, setting solo links to NULL."""
        # Setup Group with 1 member linked to a Soloist
        with database.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("INSERT INTO artists (name, artist_type) VALUES ('Group A', 'group')")
            grp_id = cur.lastrowid
            cur.execute("INSERT INTO artists (name, artist_type) VALUES ('Soloist B', 'solo')")
            solo_id = cur.lastrowid

            # Add album and track for Group A
            cur.execute("INSERT INTO albums (artist_id, title) VALUES (?, 'Album 1')", (grp_id,))
            alb_id = cur.lastrowid
            cur.execute("INSERT INTO tracks (album_id, title, track_number) VALUES (?, 'Song 1', 1)", (alb_id,))

            # Link Soloist B to Group A
            cur.execute("""
                INSERT INTO artist_members (group_id, member_name, solo_artist_id, is_former, display_order)
                VALUES (?, 'Soloist B', ?, 0, 1)
            """, (grp_id, solo_id))
            conn.commit()

        # Deleting Group A should cascade its albums, tracks, and member entries
        database.delete_artist_profile(grp_id)

        with database.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM artists WHERE id = ?", (grp_id,))
            self.assertEqual(cur.fetchone()[0], 0)
            cur.execute("SELECT COUNT(*) FROM albums WHERE id = ?", (alb_id,))
            self.assertEqual(cur.fetchone()[0], 0)
            cur.execute("SELECT COUNT(*) FROM tracks WHERE album_id = ?", (alb_id,))
            self.assertEqual(cur.fetchone()[0], 0)
            cur.execute("SELECT COUNT(*) FROM artist_members WHERE group_id = ?", (grp_id,))
            self.assertEqual(cur.fetchone()[0], 0)
            # Soloist B should still exist
            cur.execute("SELECT COUNT(*) FROM artists WHERE id = ?", (solo_id,))
            self.assertEqual(cur.fetchone()[0], 1)

    def test_watchlist_group_graduation_with_members(self):
        """Graduating a watchlist group preserves member roster and parses former member asterisks."""
        with database.get_connection() as conn:
            cur = conn.cursor()
            # 1. Add prospective group to watchlist with members (including 1 former with *)
            cur.execute("""
                INSERT INTO artist_watchlist (name, artist_type, generation, status, members, notes)
                VALUES ('izna', 'group', 'Gen 5', 'Active', 'Mai, Jeemin, Koko, Sarang, Jungeun, Saebi, *Jiyoon', 'Debut upcoming')
            """)
            # 2. Simulate committing first album for izna
            cur.execute("INSERT INTO artists (name, artist_type) VALUES ('izna', 'group')")
            izna_id = cur.lastrowid
            conn.commit()

            # 3. Graduate watchlist artist
            graduated = database.graduate_watchlist_artist(conn, "izna")
            self.assertTrue(graduated)
            conn.commit()

            # 4. Verify artist removed from watchlist
            cur.execute("SELECT COUNT(*) FROM artist_watchlist WHERE LOWER(name) = 'izna'")
            self.assertEqual(cur.fetchone()[0], 0)

            # 5. Verify member roster created with display order and former flags
            cur.execute("""
                SELECT member_name, is_former, display_order 
                FROM artist_members 
                WHERE group_id = ? 
                ORDER BY display_order ASC
            """, (izna_id,))
            roster = cur.fetchall()
            self.assertEqual(len(roster), 7)
            # Check active members
            for r in roster[:6]:
                self.assertEqual(r["is_former"], 0)
            self.assertEqual(roster[0]["member_name"], "Mai")
            # Check former member (* stripped and is_former=1)
            self.assertEqual(roster[6]["member_name"], "Jiyoon")
            self.assertEqual(roster[6]["is_former"], 1)

    def test_watchlist_crud_operations(self):
        """Watchlist supports member rosters, notes, and CRUD operations."""
        # 1. Test add and get watchlist artist
        wl_id = database.add_watchlist_artist({
            "name": "MEOVV",
            "artist_type": "group",
            "generation": "Gen 5",
            "status": "Active",
            "members": "Sooin, Gawon, Anna, Narin, Ella",
            "notes": "The Black Label girl group"
        })
        self.assertGreater(wl_id, 0)

        items = database.get_watchlist_artists()
        meovv = next((i for i in items if i["name"] == "MEOVV"), None)
        self.assertIsNotNone(meovv)
        self.assertEqual(meovv["members"], "Sooin, Gawon, Anna, Narin, Ella")
        self.assertEqual(meovv["notes"], "The Black Label girl group")

        # 2. Test update watchlist artist
        database.update_watchlist_artist(wl_id, {
            "name": "MEOVV",
            "artist_type": "group",
            "generation": "Gen 5",
            "status": "Active",
            "members": "Sooin, Gawon, Anna, Narin, Ella, *Guest",
            "notes": "Updated note with debut single"
        })
        items_updated = database.get_watchlist_artists()
        meovv_up = next((i for i in items_updated if i["id"] == wl_id), None)
        self.assertIn("*Guest", meovv_up["members"])
        self.assertEqual(meovv_up["notes"], "Updated note with debut single")
        self.assertIsNone(meovv_up["predicted_score"])

        # 3. Test persisting STC taste prediction
        ok = database.update_watchlist_prediction(wl_id, 4.15, 4, 5, '[{"title": "MEOW", "blend": 4.30}]')
        self.assertTrue(ok)
        meovv_pred = next((i for i in database.get_watchlist_artists() if i["id"] == wl_id), None)
        self.assertAlmostEqual(meovv_pred["predicted_score"], 4.15, places=2)
        self.assertEqual(meovv_pred["predicted_hits"], 4)
        self.assertEqual(meovv_pred["predicted_total"], 5)
        self.assertIn("MEOW", meovv_pred["predicted_tracks_json"])

    def test_update_artist_profile_type_switching_cleans_orphaned_rows(self):
        """Bug 3.9.1: Switching artist_type group <-> solo must clean orphaned roster and soloist rows."""
        # 1. Create a group with roster members
        group_id = database.create_artist_profile({
            "name": "Test Group",
            "artist_type": "group",
            "members": ["Member 1", "Member 2"]
        })
        with database.get_connection() as conn:
            members = database.get_artist_members(conn, group_id)
            self.assertEqual(len(members), 2)

        # 2. Switch group to solo
        database.update_artist_profile(group_id, {
            "name": "Test Group",
            "artist_type": "solo",
            "parent_groups": ""
        })
        with database.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM artist_members WHERE group_id = ?", (group_id,))
            count = cur.fetchone()[0]
            self.assertEqual(count, 0, "Old group roster rows must be removed when switching to solo")

        # 3. Create a parent group and link this soloist
        parent_id = database.create_artist_profile({
            "name": "Parent Group",
            "artist_type": "group",
            "members": []
        })
        database.update_artist_profile(group_id, {
            "name": "Test Group",
            "artist_type": "solo",
            "parent_groups": "Parent Group"
        })
        with database.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM artist_members WHERE solo_artist_id = ?", (group_id,))
            self.assertEqual(cur.fetchone()[0], 1)

        # 4. Switch soloist back to group
        database.update_artist_profile(group_id, {
            "name": "Test Group",
            "artist_type": "group",
            "members": ["New Member"]
        })
        with database.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM artist_members WHERE solo_artist_id = ?", (group_id,))
            self.assertEqual(cur.fetchone()[0], 0, "Stale soloist links must be nullified when switching to group")

    def test_sync_soloist_affiliations_renaming_soloist_updates_member_name(self):
        """Bug 3.9.2: Renaming a linked soloist must update member_name in artist_members."""
        with database.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("INSERT INTO artists (name, artist_type) VALUES ('Parent Group', 'group')")
            grp_id = cur.lastrowid
            cur.execute("INSERT INTO artists (name, artist_type) VALUES ('OldStageName', 'solo')")
            solo_id = cur.lastrowid

            # Initial link
            database.sync_soloist_affiliations(conn, solo_id, "OldStageName", "Parent Group")
            conn.commit()

            cur.execute("SELECT member_name, solo_artist_id FROM artist_members WHERE group_id = ?", (grp_id,))
            row = cur.fetchone()
            self.assertEqual(row["member_name"], "OldStageName")
            self.assertEqual(row["solo_artist_id"], solo_id)

            # Rename soloist
            database.sync_soloist_affiliations(conn, solo_id, "NewStageName", "Parent Group")
            conn.commit()

            cur.execute("SELECT member_name, solo_artist_id FROM artist_members WHERE group_id = ?", (grp_id,))
            row_updated = cur.fetchone()
            self.assertEqual(row_updated["member_name"], "NewStageName", "member_name must update when soloist is renamed")
            self.assertEqual(row_updated["solo_artist_id"], solo_id)

    def test_unicode_case_insensitive_soloist_linking(self):
        """Backlog #19: Unicode characters like 'ROSÉ' vs 'Rosé' must auto-link cleanly."""
        with database.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("INSERT INTO artists (name, artist_type) VALUES ('BLACKPINK', 'group')")
            bp_id = cur.lastrowid
            cur.execute("INSERT INTO artists (name, artist_type) VALUES ('ROSÉ', 'solo')")
            rose_id = cur.lastrowid

            # Group roster uses mixed case 'Rosé' while solo profile is 'ROSÉ'
            database.sync_artist_members(conn, bp_id, ["Jisoo", "Jennie", "Rosé", "Lisa"])
            conn.commit()

            cur.execute("SELECT solo_artist_id FROM artist_members WHERE group_id = ? AND member_name = 'Rosé'", (bp_id,))
            row = cur.fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["solo_artist_id"], rose_id)

    def test_soloist_aggregates_parent_group_solo_and_unit_tracks(self):
        """Backlog #19: Soloists aggregate matching solo/unit tracks from parent group albums with full parity."""
        with database.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("INSERT INTO artists (name, artist_type, debut_year) VALUES ('ITZY', 'group', 2019)")
            grp_id = cur.lastrowid
            cur.execute("INSERT INTO artists (name, artist_type, debut_year) VALUES ('YEJI', 'solo', 2025)")
            yeji_id = cur.lastrowid
            database.sync_artist_members(conn, grp_id, ["Yeji", "Lia", "Ryujin"])

            # Group album with Yeji solo, Lia solo, and a core track
            cur.execute("INSERT INTO albums (artist_id, title, album_type, release_date) VALUES (?, 'BORN TO BE', 'LP', '2024-01-08')", (grp_id,))
            grp_alb_id = cur.lastrowid
            cur.execute("INSERT INTO tracks (album_id, title, track_number, track_type, rating, notes) VALUES (?, 'UNTOUCHABLE', 1, 'core', 4.0, '')", (grp_alb_id,))
            cur.execute("INSERT INTO tracks (album_id, title, track_number, track_type, rating, notes) VALUES (?, 'Crown on My Head (Yeji)', 2, 'solo', 5.0, 'Yeji Solo')", (grp_alb_id,))
            cur.execute("INSERT INTO tracks (album_id, title, track_number, track_type, rating, notes) VALUES (?, 'Blossom (Lia)', 3, 'solo', 2.0, 'Lia Solo')", (grp_alb_id,))
            # Ensure word-boundary guard: 'Yeji' should not match 'Yejisoo'
            cur.execute("INSERT INTO tracks (album_id, title, track_number, track_type, rating, notes) VALUES (?, 'Unit Track', 4, 'unit', 1.0, 'Yejisoo Unit')", (grp_alb_id,))

            # Soloist's own EP
            cur.execute("INSERT INTO albums (artist_id, title, album_type, release_date) VALUES (?, 'AIR', 'EP', '2025-03-10')", (yeji_id,))
            solo_alb_id = cur.lastrowid
            cur.execute("INSERT INTO tracks (album_id, title, track_number, track_type, rating, is_title_track) VALUES (?, 'Air', 1, 'core', 4.0, 1)", (solo_alb_id,))
            cur.execute("INSERT INTO tracks (album_id, title, track_number, track_type, rating, is_title_track) VALUES (?, 'Invasion', 2, 'core', 4.0, 0)", (solo_alb_id,))
            conn.commit()

        deep = database.get_artist_summary_metrics("YEJI")
        dash = next(d for d in database.get_all_artists_summary() if d["artist"] == "YEJI")

        # Artist Only (group_* fields) stays strictly on YEJI's own 2 tracks (avg 4.0, 0 perfects)
        self.assertEqual(deep["group_tracks_count"], 2)
        self.assertEqual(deep["group_avg"], 4.0)
        self.assertEqual(deep["group_perfect"], 0)

        # All (+Solo/Unit) includes Crown on My Head (5.0) -> 3 tracks, avg (4+4+5)/3 = 4.33, 1 perfect
        self.assertEqual(deep["total_rated_tracks"], 3)
        self.assertEqual(deep["raw_total_tracks"], 3)
        self.assertEqual(deep["overall_avg"], 4.33)
        self.assertEqual(deep["overall_perfect"], 1)

        # Zero drift between Deep Dive and All-Artist Dashboard
        self.assertEqual(dash["total_rated_tracks"], deep["total_rated_tracks"])
        self.assertEqual(dash["overall_avg"], deep["overall_avg"])
        self.assertEqual(dash["compacted_qwar"], deep["compacted_qwar"])
        self.assertEqual(dash["qwar_per_track"], deep["qwar_per_track"])

    def test_unlinked_parent_groups_persisted_and_auto_linked_on_group_import(self):
        """Unregistered parent groups persist as dummy badges (group_id=None) and auto-link when imported later."""
        hyuna_id = database.create_artist_profile({
            "name": "HyunA",
            "artist_type": "solo",
            "parent_groups": "*Wonder Girls, *4MINUTE"
        })
        # Neither group exists yet -> both returned with group_id=None
        deep_before = database.get_artist_summary_metrics("HyunA")
        self.assertEqual(
            [(pg["group_name"], pg["group_id"], bool(pg["is_former"])) for pg in deep_before["parent_groups"]],
            [("Wonder Girls", None, True), ("4MINUTE", None, True)]
        )

        # Now import 4MINUTE later
        four_id = database.create_artist_profile({
            "name": "4MINUTE",
            "artist_type": "group",
            "debut_year": 2009
        })
        deep_after = database.get_artist_summary_metrics("HyunA")
        by_name = {pg["group_name"]: pg for pg in deep_after["parent_groups"]}
        self.assertEqual(by_name["4MINUTE"]["group_id"], four_id)
        self.assertTrue(bool(by_name["4MINUTE"]["is_former"]))
        self.assertIsNone(by_name["Wonder Girls"]["group_id"])
        self.assertTrue(bool(by_name["Wonder Girls"]["is_former"]))

        # Also verify get_all_artist_profiles and get_all_artists_summary include both
        prof = next(p for p in database.get_all_artist_profiles() if p["id"] == hyuna_id)
        self.assertEqual(len(prof["parent_groups"]), 2)
        dash = next(d for d in database.get_all_artists_summary() if d["artist"] == "HyunA")
        self.assertEqual(len(dash["parent_groups"]), 2)


if __name__ == "__main__":
    unittest.main()
