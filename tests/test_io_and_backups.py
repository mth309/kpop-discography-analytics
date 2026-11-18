"""
Regression tests for Library Management Import/Export tools, WAL checkpointing,
database/JSON exports, and CSV export round-trip parity (Backlog Items #9 and #11).
"""

import csv
import io
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app
import backup
import database

TEST_DB_FILE = os.path.join(tempfile.gettempdir(), "test_io_and_backups.db")


def setUpModule():
    for suffix in ["", "-wal", "-shm"]:
        p = TEST_DB_FILE + suffix
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass
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


class TestIOAndBackups(unittest.TestCase):
    """Verifies Phase 2 I/O data export endpoints, WAL safety, and CSV parity."""

    def setUp(self):
        """Clean tables and seed controlled catalog test fixtures before each test."""
        with database.get_connection() as conn:
            conn.execute("DELETE FROM tracks")
            conn.execute("DELETE FROM albums")
            conn.execute("DELETE FROM artist_members")
            conn.execute("DELETE FROM artist_watchlist")
            conn.execute("DELETE FROM artists")
            conn.commit()

        # Seed 1 Group artist with roster
        with database.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO artists (name, debut_year, generation, status, artist_type)
                VALUES ('IVE', 2021, 'Gen 4', 'Active', 'group')
            """)
            ive_id = cur.lastrowid
            database.sync_artist_members(conn, ive_id, ["Yujin", "Gaeul", "Rei", "Wonyoung", "Liz", "Leeseo"])

            # Seed 1 Solo artist affiliated with Girls' Generation
            cur.execute("""
                INSERT INTO artists (name, debut_year, generation, status, artist_type)
                VALUES ("Girls' Generation", 2007, 'Gen 2', 'Active', 'group')
            """)
            snsd_id = cur.lastrowid
            database.sync_artist_members(conn, snsd_id, ["Taeyeon", "Yoona", "*Jessica"])

            cur.execute("""
                INSERT INTO artists (name, debut_year, generation, status, artist_type)
                VALUES ('Taeyeon', 2015, 'Gen 2', 'Active', 'solo')
            """)
            taeyeon_id = cur.lastrowid
            database.sync_soloist_affiliations(conn, taeyeon_id, "Taeyeon", "Girls' Generation")

            # Seed Album with MB Release Group ID
            cur.execute("""
                INSERT INTO albums (artist_id, title, album_type, release_date, mb_release_group_id)
                VALUES (?, 'I''VE MINE', 'EP', '2023-10-13', 'mb-rg-uuid-1234')
            """, (ive_id,))
            album_id = cur.lastrowid

            # Seed Tracks including formula injection strings and special types
            cur.execute("""
                INSERT INTO tracks (album_id, track_number, title, rating, is_title_track, track_type, notes)
                VALUES (?, 1, 'Baddie', 5.0, 1, 'core', '=1+1 Dangerous Formula')
            """, (album_id,))
            cur.execute("""
                INSERT INTO tracks (album_id, track_number, title, rating, is_title_track, track_type, notes)
                VALUES (?, 2, 'Off The Record', 4.5, 0, 'core', '+CMD() Test Note')
            """, (album_id,))
            cur.execute("""
                INSERT INTO tracks (album_id, track_number, title, rating, is_title_track, track_type, notes)
                VALUES (?, 3, 'Baddie (Inst.)', NULL, 0, 'non_canon', '-Minus Prefix')
            """, (album_id,))

            # Seed Watchlist Entry
            cur.execute("""
                INSERT INTO artist_watchlist (name, artist_type, generation, status, date_added, notes)
                VALUES ('tripleS', 'group', 'Gen 5', 'Active', '2026-01-01', '24 member group')
            """)
            conn.commit()

    def test_wal_checkpoint(self):
        """Verifies checkpoint_wal flushes WAL journal frames without error."""
        try:
            database.checkpoint_wal()
        except Exception as e:
            self.fail(f"checkpoint_wal raised an unexpected exception: {e}")

    def test_json_export_structure_and_counts(self):
        """Verifies full catalog JSON export captures all 5 tables and metadata."""
        data = database.get_full_catalog_json_data()

        self.assertIn("metadata", data)
        self.assertIn("artists", data)
        self.assertIn("albums", data)
        self.assertIn("tracks", data)
        self.assertIn("artist_members", data)
        self.assertIn("artist_watchlist", data)

        counts = data["metadata"]["counts"]
        self.assertEqual(counts["artists"], 3)  # IVE, SNSD, Taeyeon
        self.assertEqual(counts["albums"], 1)
        self.assertEqual(counts["tracks"], 3)
        self.assertEqual(counts["artist_watchlist"], 1)

        # Verify API response
        api_res = app.export_json()
        self.assertEqual(api_res.media_type, "application/json")
        parsed = json.loads(api_res.body)
        self.assertEqual(parsed["metadata"]["counts"]["artists"], 3)
        self.assertEqual(parsed["tracks"][0]["title"], "Baddie")

    def test_db_export_response(self):
        """Verifies /api/export/db checkpoints WAL and returns downloadable .db FileResponse."""
        res = app.export_db()
        self.assertTrue(os.path.exists(res.path))
        self.assertTrue(res.filename.startswith("kpop_ratings_"))
        self.assertTrue(res.filename.endswith(".db"))
        self.assertIn("sqlite3", res.media_type)

    def test_csv_export_headers_and_column_alignment(self):
        """Verifies CSV export contains all 17 schema columns in exact alignment (Backlog #9)."""
        res = app.export_csv()
        content = res.body.decode("utf-8")
        reader = csv.reader(io.StringIO(content))
        rows = list(reader)

        expected_headers = [
            "Artist", "Artist Type", "Affiliated Groups", "Generation", "Debut Year", "Artist Status", "Members",
            "Album Title", "Album Type", "Release Date", "MB Release Group ID",
            "Track Number", "Track Title", "Track Type", "Title Track", "Rating", "Notes"
        ]
        self.assertEqual(rows[0], expected_headers)
        self.assertEqual(len(rows[0]), 17)

        # Verify Track 1 Row: IVE - Baddie
        track1 = rows[1]
        self.assertEqual(len(track1), 17)
        self.assertEqual(track1[0], "IVE")
        self.assertEqual(track1[1], "group")
        self.assertEqual(track1[2], "")  # No affiliated groups for group
        self.assertEqual(track1[3], "Gen 4")
        self.assertEqual(track1[4], "2021")
        self.assertEqual(track1[5], "Active")
        self.assertIn("Wonyoung", track1[6])
        self.assertEqual(track1[7], "I'VE MINE")
        self.assertEqual(track1[8], "EP")
        self.assertEqual(track1[9], "2023-10-13")
        self.assertEqual(track1[10], "mb-rg-uuid-1234")
        self.assertEqual(track1[11], "1")
        self.assertEqual(track1[12], "Baddie")
        self.assertEqual(track1[13], "core")
        self.assertEqual(track1[14], "Yes")
        self.assertEqual(track1[15], "5.0")

    def test_csv_sanitization_and_formula_injection(self):
        """Verifies leading formula triggers (=, +, -, @) are sanitized with single quote prefix."""
        res = app.export_csv()
        content = res.body.decode("utf-8")
        reader = csv.reader(io.StringIO(content))
        rows = list(reader)

        # Row 1 notes: '=1+1 Dangerous Formula' -> should start with "'="
        row1_notes = rows[1][16]
        self.assertTrue(row1_notes.startswith("'="), f"Expected formula quote prefix, got: {row1_notes}")

        # Row 2 notes: '+CMD() Test Note' -> should start with "'+'
        row2_notes = rows[2][16]
        self.assertTrue(row2_notes.startswith("'+"), f"Expected formula quote prefix, got: {row2_notes}")

        # Row 3 notes: '-Minus Prefix' -> should start with "'-"
        row3_notes = rows[3][16]
        self.assertTrue(row3_notes.startswith("'-"), f"Expected formula quote prefix, got: {row3_notes}")

    def test_app_snapshot_endpoint_and_backup_creation(self):
        """Verifies /api/backup/snapshot triggers backup.create_snapshot and returns folder metadata."""
        with tempfile.TemporaryDirectory() as temp_backup_dir:
            temp_path = Path(temp_backup_dir)
            with patch.object(backup, "BACKUP_DIR", temp_path):
                payload = app.SnapshotIn(label="test-unit-checkpoint")
                result = app.api_create_snapshot(payload)

                self.assertEqual(result["status"], "success")
                self.assertIn("test-unit-checkpoint", result["snapshot_folder"])
                created_dir = Path(result["path"])
                self.assertTrue(created_dir.exists())
                self.assertTrue((created_dir / "database.py").exists())

                # Bug 3.7: Active test database must be backed up dynamically, never live kpop_ratings.db
                active_db_name = Path(database.DB_NAME).name
                self.assertTrue((created_dir / active_db_name).exists())
                self.assertTrue((temp_path / active_db_name).exists())
                self.assertFalse((created_dir / "kpop_ratings.db").exists())

    def test_json_restore_preserves_track_ids_and_syncs_sequence(self):
        """Backlog #18: JSON restore must preserve original track IDs for SonicTaste and sync sqlite_sequence."""
        exported = database.get_full_catalog_json_data()
        custom_ids = [42, 99, 150]
        for track, custom_id in zip(exported["tracks"], custom_ids):
            track["id"] = custom_id

        # Wipe database before restoring
        with database.get_connection() as conn:
            conn.execute("DELETE FROM tracks")
            conn.execute("DELETE FROM albums")
            conn.execute("DELETE FROM artist_members")
            conn.execute("DELETE FROM artist_watchlist")
            conn.execute("DELETE FROM artists")
            conn.commit()

        counts = database.restore_catalog_from_json(exported)
        self.assertEqual(counts["tracks"], 3)
        self.assertEqual(counts["artists"], 3)
        self.assertEqual(counts["albums"], 1)
        self.assertEqual(counts["artist_watchlist"], 1)

        with database.get_connection() as conn:
            restored_ids = [r["id"] for r in conn.execute("SELECT id FROM tracks ORDER BY id ASC").fetchall()]
            self.assertEqual(restored_ids, custom_ids)

            # Verify sqlite_sequence was synced so next AUTOINCREMENT insert > 150
            album_id = conn.execute("SELECT id FROM albums LIMIT 1").fetchone()["id"]
            cur = conn.execute(
                "INSERT INTO tracks (album_id, track_number, title, rating, is_title_track, track_type) VALUES (?, 4, 'Payback', 4.5, 0, 'core')",
                (album_id,),
            )
            self.assertGreater(cur.lastrowid, 150)
            conn.commit()

    def test_json_restore_rolls_back_on_fk_or_validation_error(self):
        """JSON restore must roll back atomically on foreign-key violations without wiping existing data."""
        exported = database.get_full_catalog_json_data()
        exported["tracks"][0]["album_id"] = 999999  # Orphan FK

        with self.assertRaises(ValueError):
            database.restore_catalog_from_json(exported)

        # Original seeded catalog must remain intact
        with database.get_connection() as conn:
            track_count = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
            self.assertEqual(track_count, 3)

    def test_db_restore_preserves_track_ids_and_rejects_invalid_db(self):
        """DB restore via Connection.backup() must preserve track IDs and reject invalid/incomplete files."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            src_db = Path(tmp_dir) / "source_backup.db"
            shutil_src = Path(database.DB_NAME)
            database.checkpoint_wal()
            src_conn = sqlite3.connect(src_db)
            with database.get_connection() as cur_conn:
                cur_conn.backup(src_conn)
            src_conn.execute("UPDATE tracks SET id = 777 WHERE title = 'Baddie'")
            src_conn.commit()
            src_conn.close()

            # Mutate live test DB so we can prove restore overwrites it
            with database.get_connection() as conn:
                conn.execute("DELETE FROM tracks")
                conn.commit()

            counts = database.restore_catalog_from_db(src_db)
            self.assertEqual(counts["tracks"], 3)

            with database.get_connection() as conn:
                baddie_row = conn.execute("SELECT id FROM tracks WHERE title = 'Baddie'").fetchone()
                self.assertIsNotNone(baddie_row)
                self.assertEqual(baddie_row["id"], 777)

            # Empty/invalid DB missing required tables must raise ValueError
            empty_db = Path(tmp_dir) / "empty.db"
            sqlite3.connect(empty_db).close()
            with self.assertRaises(ValueError):
                database.restore_catalog_from_db(empty_db)

    def test_api_restore_endpoint_json_and_db_creates_pre_restore_snapshot(self):
        """POST /api/restore must create a pre_restore snapshot and restore both .json and .db payloads."""
        class _FakeRequest:
            def __init__(self, raw_bytes: bytes):
                self._raw = raw_bytes

            async def body(self) -> bytes:
                return self._raw

        def _run_coro(coro):
            try:
                while True:
                    coro.send(None)
            except StopIteration as stop:
                return stop.value

        exported = database.get_full_catalog_json_data()
        exported["tracks"][0]["id"] = 555
        json_bytes = json.dumps(exported).encode("utf-8")

        with tempfile.TemporaryDirectory() as temp_backup_dir:
            temp_path = Path(temp_backup_dir)
            with patch.object(backup, "BACKUP_DIR", temp_path):
                res = _run_coro(app.api_restore_catalog(_FakeRequest(json_bytes), filename="catalog_backup.json"))
                self.assertEqual(res["status"], "success")
                self.assertEqual(res["format"], "json")
                self.assertIn("pre_restore", res["snapshot_folder"])
                self.assertEqual(res["counts"]["tracks"], 3)
                self.assertTrue((temp_path / "snapshots" / res["snapshot_folder"]).exists())

                with database.get_connection() as conn:
                    ids = [r["id"] for r in conn.execute("SELECT id FROM tracks").fetchall()]
                    self.assertIn(555, ids)

                # Now test .db upload via the same endpoint
                database.checkpoint_wal()
                db_bytes = Path(database.DB_NAME).read_bytes()
                res_db = _run_coro(app.api_restore_catalog(_FakeRequest(db_bytes), filename="kpop_ratings.db"))
                self.assertEqual(res_db["status"], "success")
                self.assertEqual(res_db["format"], "db")
                self.assertIn("pre_restore", res_db["snapshot_folder"])
                self.assertEqual(res_db["counts"]["tracks"], 3)


if __name__ == "__main__":
    unittest.main()
