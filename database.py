"""SQLite Database Access, Analytics Calculations, and Catalog Invariants Engine.

This module is the core persistence layer of the K-Pop App. It manages:
- Database schema initialization, migrations, and foreign key cascades.
- Album staging, track commit, and deduplication idempotency.
- Group member rosters, former member markers (*), and soloist affiliations.
- Prospective artist discovery watchlist queue and automatic graduation.
- Advanced career analytics: canonical track deduplication (CANONICAL_UNIQUE_TRACK_SQL),
  exponential moving averages (EMA), career productivity pacing, and the
  Quality-Weighted WAR (Q-WAR) statistical ranking engine.
"""

import math
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple, Union
from fetcher import ALT_LANG_PATTERN

IS_FROZEN = getattr(sys, "frozen", False)
_SOURCE_DIR = Path(__file__).resolve().parent
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", _SOURCE_DIR))
DATA_DIR = Path(sys.executable).resolve().parent if IS_FROZEN else _SOURCE_DIR
BASE_DIR = DATA_DIR
DB_NAME = str(DATA_DIR / "kpop_ratings.db")


# ===========================================================================
# 1. DATABASE CONNECTION & SCHEMA INITIALIZATION
# ===========================================================================

def is_alt_lang_text(text: Optional[str]) -> int:
    """Evaluates whether a title string indicates a localized alternate-language edition.

    Re-uses fetcher.ALT_LANG_PATTERN to ensure consistent regex matching across the app.
    """
    return 1 if text and ALT_LANG_PATTERN.search(text) else 0

class _ClosingConnection(sqlite3.Connection):
    """SQLite connection that commits/rolls back AND closes on `with` block exit."""

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            return super().__exit__(exc_type, exc_val, exc_tb)
        finally:
            self.close()


def get_connection() -> sqlite3.Connection:
    """Returns a SQLite connection with foreign keys enabled and dict-like row access.

    Returns:
        sqlite3.Connection: Active database connection configured with `row_factory = sqlite3.Row`.
    """
    conn = sqlite3.connect(DB_NAME, factory=_ClosingConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.create_function("is_alt_lang", 1, is_alt_lang_text, deterministic=True)
    conn.create_function("release_day_gap", 2, release_day_gap, deterministic=True)
    conn.create_function("py_lower", 1, lambda s: s.replace("\u2019", "'").replace("\u2018", "'").casefold() if isinstance(s, str) else s, deterministic=True)
    return conn


def resolve_career_start_date(earliest_release_str: Optional[str], debut_year: Optional[int] = None) -> Optional[date]:
    """Normalizes an earliest release date string or debut year into a Python date object.

    Handles partial formats ('YYYY', 'YYYY-MM', 'YYYY-MM-DD') and falls back to Jan 1
    of debut_year if no album release dates exist.
    """
    if earliest_release_str:
        try:
            parts = str(earliest_release_str).strip().split("-")
            return date(int(parts[0]), int(parts[1]) if len(parts) > 1 else 1, int(parts[2]) if len(parts) > 2 else 1)
        except Exception:
            pass
    if debut_year:
        try:
            return date(int(debut_year), 1, 1)
        except Exception:
            pass
    return None


def release_day_gap(date_target: Optional[str], date_source: Optional[str]) -> Optional[int]:
    """Computes the difference in calendar days between two release date strings (target - source).

    Handles partial dates ('YYYY', 'YYYY-MM') by normalizing to the first of the month/year.
    """
    d1 = resolve_career_start_date(date_target)
    d2 = resolve_career_start_date(date_source)
    if not d1 or not d2:
        return None
    return (d1 - d2).days


def _round_half_up(val: Optional[float], decimals: int = 2) -> Optional[float]:
    """Rounds half away from zero to match SQLite's ROUND(..., decimals) behavior."""
    if val is None:
        return None
    factor = 10.0 ** decimals
    return math.floor(val * factor + 0.5) / factor

def init_db(schema_file: Optional[Union[str, Path]] = None) -> None:
    """Initializes tables, constraints, and indexes from schema.sql and enables WAL mode.

    Args:
        schema_file: Optional path override to schema.sql (used for test database initialization).
    """
    schema_path = Path(schema_file) if schema_file else (RESOURCE_DIR / "schema.sql")
    with open(schema_path, "r", encoding="utf-8") as f:
        schema = f.read()
    with get_connection() as conn:
        conn.executescript(schema)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(artists)").fetchall()}
        if "parent_groups" not in cols:
            conn.execute("ALTER TABLE artists ADD COLUMN parent_groups TEXT")
        conn.commit()
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA user_version = 1;")
        conn.commit()


def checkpoint_wal() -> None:
    """Forces all SQLite WAL journal pages to flush into the main database file.

    Ensures zero pending write lag before database file backups or downloads.
    """
    with get_connection() as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        conn.commit()


# ===========================================================================
# 2. ALBUM & TRACK CATALOG OPERATIONS
# ===========================================================================

def get_or_create_artist(conn: sqlite3.Connection, name: str) -> int:
    """Finds or inserts an artist by name, returning their primary key ID.

    Performs case-insensitive matching before inserting a new record.

    Args:
        conn: Active SQLite connection.
        name: Artist name string.

    Returns:
        int: Primary key ID of the existing or newly created artist.
    """
    cursor = conn.cursor()
    clean_name = name.strip()
    cursor.execute("SELECT id FROM artists WHERE py_lower(name) = py_lower(?)", (clean_name,))
    row = cursor.fetchone()
    if row:
        return row["id"]
    cursor.execute("INSERT INTO artists (name) VALUES (?)", (clean_name,))
    new_id = cursor.lastrowid
    _auto_link_soloists_for_group(conn, new_id, clean_name)
    return new_id

def add_album(
    conn: sqlite3.Connection, 
    artist_id: int, 
    title: str, 
    release_date: Optional[str] = None, 
    album_type: str = "EP",
    mb_release_group_id: Optional[str] = None
) -> int:
    """Inserts an album record or returns the existing album ID if already present.

    Prevents duplicate album entries by matching against MusicBrainz release group ID
    first, and falling back to case-insensitive title and album_type.

    Args:
        conn: Active SQLite connection.
        artist_id: Primary key ID of the artist.
        title: Album title string.
        release_date: Optional release date string (YYYY-MM-DD or partial).
        album_type: Album classification tier ('EP', 'LP', or 'Single').
        mb_release_group_id: Optional MusicBrainz release-group UUID string.

    Returns:
        int: Primary key ID of the inserted or existing album.
    """
    cursor = conn.cursor()
    # Try inserting the album record
    cursor.execute(
        """
        INSERT OR IGNORE INTO albums (artist_id, title, release_date, album_type, mb_release_group_id)
        VALUES (?, ?, ?, ?, ?)
        """,
        (artist_id, title.strip(), release_date, album_type, mb_release_group_id),
    )
    # If it already existed, fetch by MusicBrainz release group ID, or artist_id, title AND album_type
    if cursor.rowcount == 0:
        # Match by MBID first if available (scoped to this artist to prevent cross-artist collision on shared release groups)
        if mb_release_group_id:
            cursor.execute(
                "SELECT id FROM albums WHERE mb_release_group_id = ? AND artist_id = ?",
                (mb_release_group_id, artist_id)
            )
            row = cursor.fetchone()
            if row:
                return row["id"]

        # Match by artist_id, title and album_type
        cursor.execute(
            """
            SELECT id FROM albums 
            WHERE artist_id = ? AND LOWER(title) = LOWER(?) AND album_type = ?
            """,
            (artist_id, title.strip(), album_type)
        )
        row = cursor.fetchone()
        if row:
            return row["id"]

        cursor.execute(
            "SELECT id FROM albums WHERE artist_id = ? AND LOWER(title) = LOWER(?)", 
            (artist_id, title.strip())
        )
        return cursor.fetchone()["id"]

    return cursor.lastrowid

def get_album_details(album_id: int) -> Optional[Dict[str, Any]]:
    """Loads an album and its complete tracklist for staging and editing.

    Args:
        album_id: Primary key ID of the album.

    Returns:
        Optional[Dict[str, Any]]: Dictionary containing:
            - id (int): Album primary key.
            - title (str): Album title.
            - release_date (str): Release date.
            - album_type (str): 'EP', 'LP', or 'Single'.
            - artist (str): Artist name.
            - tracks (List[dict]): List of track records sorted by track_number.
            Returns None if album is not found.
    """
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT al.id, al.title, al.release_date, al.album_type, ar.name as artist
            FROM albums al
            JOIN artists ar ON al.artist_id = ar.id
            WHERE al.id = ?
        """, (album_id,))
        album_row = cur.fetchone()
        if not album_row:
            return None
        
        cur.execute("""
            SELECT id, track_number, title, rating, is_title_track, track_type, notes
            FROM tracks
            WHERE album_id = ?
            ORDER BY track_number ASC
        """, (album_id,))
        tracks = [dict(r) for r in cur.fetchall()]
        
        data = dict(album_row)
        data["tracks"] = tracks
        return data

def commit_album_payload(payload: Dict[str, Any], album_id: Optional[int] = None) -> int:
    """Commits an album payload and its ordered tracklist to the database.

    Handles both brand-new album imports and updates to existing albums:
    - If `album_id` is supplied (or present in `payload`), updates album metadata
      and cleanly replaces existing tracks to guarantee idempotency.
    - If new, creates or looks up the album via `add_album()`.
    - Automatically graduates matching prospective artists from `artist_watchlist`.

    Args:
        payload: Album dictionary containing 'artist', 'title', 'release_date',
            'album_type', 'mb_release_group_id', and a list of track dicts under 'tracks'.
        album_id: Optional album primary key ID to update.

    Returns:
        int: Primary key ID of the saved or updated album.
    """
    artist_name = payload["artist"].strip()
    album_title = payload["title"].strip()
    release_date = payload.get("release_date")
    album_type = payload.get("album_type", "EP")
    mb_release_group_id = payload.get("mb_release_group_id")
    tracks = payload.get("tracks", [])

    # Use explicit argument if passed (from PUT route), or payload key (from staging)
    target_album_id = album_id or payload.get("album_id")

    with get_connection() as conn:
        cur = conn.cursor()
        artist_id = get_or_create_artist(conn, artist_name)

        if target_album_id:
            cur.execute(
                """
                UPDATE albums 
                SET artist_id = ?, title = ?, release_date = ?, album_type = ?, 
                    mb_release_group_id = COALESCE(?, mb_release_group_id)
                WHERE id = ?
                """,
                (artist_id, album_title, release_date, album_type, mb_release_group_id, target_album_id)
            )
            cur.execute("DELETE FROM tracks WHERE album_id = ?", (target_album_id,))
            saved_album_id = target_album_id
        else:
            saved_album_id = add_album(
                conn=conn,
                artist_id=artist_id,
                title=album_title,
                release_date=release_date,
                album_type=album_type,
                mb_release_group_id=mb_release_group_id
            )

        for idx, track in enumerate(tracks, start=1):
            clean_track_title = track["title"].strip()
            is_title = 1 if track.get("is_title_track") else 0
            track_type = track.get("track_type") or "core"

            cur.execute(
                """
                INSERT OR IGNORE INTO tracks 
                (album_id, track_number, title, rating, is_title_track, track_type, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    saved_album_id,
                    track.get("track_number", idx),
                    clean_track_title,
                    track.get("rating"),
                    is_title,
                    track_type,
                    track.get("notes")
                )
            )

        # Automatically graduate / remove prospective artist from watchlist upon saving
        graduate_watchlist_artist(conn, artist_name)

        conn.commit()

    return saved_album_id

def delete_album(album_id: int):
    """Deletes an album and cascades deletion of all associated tracks via foreign key.

    Args:
        album_id: Primary key ID of the album to delete.
    """
    with get_connection() as conn:
        conn.execute("DELETE FROM albums WHERE id = ?", (album_id,))
        conn.commit()


# ===========================================================================
# 3. GROUP ROSTER & SOLOIST AFFILIATIONS
# ===========================================================================

def get_artist_members(conn: sqlite3.Connection, group_id: int) -> List[Dict[str, Any]]:
    """Fetches all members for a group, indicating solo profile link and former status.

    Args:
        conn: Active SQLite connection.
        group_id: Primary key ID of the group artist.

    Returns:
        List[Dict[str, Any]]: Member records with keys:
            - id (int): Roster entry ID.
            - member_name (str): Member name.
            - solo_artist_id (Optional[int]): Linked solo artist ID.
            - is_former (int): 1 if former member, 0 if active.
            - solo_artist_name (Optional[str]): Linked solo artist name.
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT 
            m.id,
            m.member_name,
            m.solo_artist_id,
            m.is_former,
            s.name AS solo_artist_name
        FROM artist_members m
        LEFT JOIN artists s ON m.solo_artist_id = s.id
        WHERE m.group_id = ?
        ORDER BY m.is_former ASC, m.display_order ASC, m.member_name ASC
    """, (group_id,))
    return [dict(r) for r in cur.fetchall()]

def sync_artist_members(conn: sqlite3.Connection, group_id: int, members: list):
    """Replaces a group's roster with an ordered list of members.

    Supports strings where a leading '*' denotes a former member (e.g. '*Jiyoon').
    Automatically links members to existing solo artist profiles matching their clean name.

    Args:
        conn: Active SQLite connection.
        group_id: Primary key ID of the group.
        members: List of member name strings or member dictionaries.
    """
    cur = conn.cursor()
    cur.execute("DELETE FROM artist_members WHERE group_id = ?", (group_id,))
    for idx, m in enumerate(members, start=1):
        if isinstance(m, str):
            raw_name = m.strip()
            is_former = 1 if raw_name.startswith("*") else 0
            name = raw_name.lstrip("* ").strip()
            solo_id = None
        else:
            name = m.get("name", "").strip()
            is_former = 1 if m.get("is_former") else 0
            solo_id = m.get("solo_artist_id")

        if not name:
            continue

        # Auto-link if a solo artist exists with this clean name (Unicode case-insensitive)
        if not solo_id:
            cur.execute("SELECT id FROM artists WHERE py_lower(name) = py_lower(?) AND artist_type = 'solo'", (name,))
            row = cur.fetchone()
            if row:
                solo_id = row["id"]

        cur.execute("""
            INSERT OR IGNORE INTO artist_members (group_id, member_name, solo_artist_id, display_order, is_former)
            VALUES (?, ?, ?, ?, ?)
        """, (group_id, name, solo_id, idx, is_former))

def _parse_parent_groups_str(parent_groups_str: Optional[str]) -> List[Tuple[str, int]]:
    """Parses comma-separated parent group names with optional '*' former prefixes.

    Returns deduplicated (group_name, is_former) tuples preserving order.
    """
    if not parent_groups_str or not parent_groups_str.strip():
        return []
    parsed: List[Tuple[str, int]] = []
    seen = set()
    for raw_token in parent_groups_str.split(","):
        token = raw_token.strip()
        if not token:
            continue
        is_former = 1 if token.startswith("*") else 0
        group_name = token.lstrip("* ").strip()
        if not group_name:
            continue
        key = group_name.casefold()
        if key in seen:
            continue
        seen.add(key)
        parsed.append((group_name, is_former))
    return parsed


def _merge_unlinked_parent_groups(
    linked_list: List[Dict[str, Any]],
    raw_parent_groups_str: Optional[str],
) -> List[Dict[str, Any]]:
    """Appends unlinked parent groups (group_id=None) from artists.parent_groups after linked ones."""
    if not raw_parent_groups_str:
        return linked_list
    linked_names = {(item.get("group_name") or "").strip().casefold() for item in linked_list}
    unlinked_active = []
    unlinked_former = []
    for g_name, is_former in _parse_parent_groups_str(raw_parent_groups_str):
        if g_name.casefold() in linked_names:
            continue
        entry = {"group_id": None, "group_name": g_name, "is_former": bool(is_former)}
        if is_former:
            unlinked_former.append(entry)
        else:
            unlinked_active.append(entry)
    return list(linked_list) + unlinked_active + unlinked_former


def _auto_link_soloists_for_group(conn: sqlite3.Connection, group_id: int, group_name: str) -> None:
    """Links any existing soloists whose saved parent_groups text references this newly added/updated group."""
    target_key = (group_name or "").strip().casefold()
    if not target_key:
        return
    cur = conn.cursor()
    cur.execute("""
        SELECT id, name, parent_groups
        FROM artists
        WHERE artist_type = 'solo' AND parent_groups IS NOT NULL AND TRIM(parent_groups) != ''
    """)
    for row in cur.fetchall():
        for g_name, is_former in _parse_parent_groups_str(row["parent_groups"]):
            if g_name.casefold() == target_key:
                cur.execute("""
                    SELECT id FROM artist_members
                    WHERE group_id = ? AND (solo_artist_id = ? OR py_lower(member_name) = py_lower(?))
                """, (group_id, row["id"], row["name"]))
                existing = cur.fetchone()
                if existing:
                    cur.execute("""
                        UPDATE artist_members
                        SET solo_artist_id = ?, is_former = ?, member_name = ?
                        WHERE id = ?
                    """, (row["id"], is_former, row["name"], existing["id"]))
                else:
                    cur.execute("""
                        INSERT INTO artist_members (group_id, member_name, solo_artist_id, is_former, display_order)
                        VALUES (?, ?, ?, ?, 99)
                    """, (group_id, row["name"], row["id"], is_former))
                break


def get_parent_groups_for_soloist(conn: sqlite3.Connection, soloist_id: int) -> List[Dict[str, Any]]:
    """Finds all parent groups linked to a soloist (plus unlinked dummy entries), ordered active first, then former.

    Args:
        conn: Active SQLite connection.
        soloist_id: Primary key ID of the solo artist.

    Returns:
        List[Dict[str, Any]]: Parent group records with keys:
            - group_id (Optional[int]): Group primary key ID, or None if not yet in catalog.
            - group_name (str): Group name.
            - is_former (bool/int): True/1 if former member of group, False/0 if active.
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT 
            g.id AS group_id, 
            g.name AS group_name,
            m.is_former
        FROM artist_members m
        JOIN artists g ON m.group_id = g.id
        WHERE m.solo_artist_id = ?
        ORDER BY m.is_former ASC, g.debut_year ASC, g.name COLLATE NOCASE ASC
    """, (soloist_id,))
    linked = [dict(r) for r in cur.fetchall()]
    cur.execute("SELECT parent_groups FROM artists WHERE id = ?", (soloist_id,))
    ar = cur.fetchone()
    raw_pg = ar["parent_groups"] if ar else None
    return _merge_unlinked_parent_groups(linked, raw_pg)


# ===========================================================================
# 4. LIBRARY DIRECTORY & PAGINATED BROWSING
# ===========================================================================

def get_paginated_library_artists(
    search: Optional[str] = None,
    generation: Optional[str] = None,
    status: Optional[str] = None,
    artist_type: Optional[str] = None,
    sort_by: str = "name_asc",
    page: int = 1,
    page_size: int = 20
) -> Dict[str, Any]:
    """Returns a server-side paginated list of artists with aggregated metrics.

    Pre-aggregates member counts in a subquery to avoid Cartesian join bloat.
    Supports filtering by text search, generation, status, and artist_type,
    along with bidirectional sorting across all directory columns.

    Args:
        search: Optional case-insensitive substring filter on artist name.
        generation: Optional generation filter ('Gen 1' to 'Gen 5' or None).
        status: Optional status filter ('Active', 'Disbanded', 'Hiatus').
        artist_type: Optional artist type filter ('group' or 'solo').
        sort_by: Column and direction string (e.g., 'name_asc', 'avg_desc').
        page: 1-indexed page number.
        page_size: Number of artist records per page.

    Returns:
        Dict[str, Any]: Dictionary containing:
            - items (List[dict]): Paginated artist summary dicts.
            - total (int): Total count of matching artists.
            - page (int): Current page index.
            - page_size (int): Current page size limit.
            - total_pages (int): Calculated total page count.
    """
    offset = (page - 1) * page_size
    query = """
        SELECT 
            a.id, 
            a.name, 
            a.debut_year, 
            a.generation, 
            a.status,
            a.artist_type,
            COUNT(DISTINCT al.id) AS album_count,
            COUNT(DISTINCT t.id) AS track_count,
            ROUND(AVG(t.rating), 2) AS avg_rating,
            SUM(CASE WHEN t.rating = 5.0 THEN 1 ELSE 0 END) AS perfect_count,
            -- Pre-aggregated member counts (prevents Cartesian product)
            COALESCE(mc.active_member_count, 0) AS active_member_count,
            COALESCE(mc.total_member_count, 0) AS total_member_count
        FROM artists a
        LEFT JOIN albums al ON a.id = al.artist_id
        LEFT JOIN tracks t ON al.id = t.album_id
        LEFT JOIN (
            SELECT 
                group_id,
                COUNT(CASE WHEN is_former = 0 THEN 1 END) AS active_member_count,
                COUNT(*) AS total_member_count
            FROM artist_members
            GROUP BY group_id
        ) mc ON a.id = mc.group_id
        WHERE 1=1
    """
    where_clause = ""
    where_params = []

    if search and search.strip():
        where_clause += " AND a.name LIKE ?"
        where_params.append(f"%{search.strip()}%")
    if generation and generation != "all":
        where_clause += " AND a.generation = ?"
        where_params.append(generation)
    if status and status != "all":
        where_clause += " AND a.status = ?"
        where_params.append(status)
    if artist_type and artist_type != "all":
        where_clause += " AND COALESCE(a.artist_type, 'group') = ?"
        where_params.append(artist_type)

    query += where_clause
    query += " GROUP BY a.id"

    sort_clauses = {
        "name_asc": " ORDER BY a.name COLLATE NOCASE ASC",
        "name_desc": " ORDER BY a.name COLLATE NOCASE DESC",
        "debut_desc": " ORDER BY a.debut_year DESC NULLS LAST, a.name COLLATE NOCASE ASC",
        "debut_asc": " ORDER BY a.debut_year ASC NULLS LAST, a.name COLLATE NOCASE ASC",
        "gen_asc": " ORDER BY a.generation ASC NULLS LAST, a.name COLLATE NOCASE ASC",
        "gen_desc": " ORDER BY a.generation DESC NULLS LAST, a.name COLLATE NOCASE ASC",
        "status_asc": " ORDER BY a.status ASC, a.name COLLATE NOCASE ASC",
        "status_desc": " ORDER BY a.status DESC, a.name COLLATE NOCASE ASC",
        "albums_asc": " ORDER BY album_count ASC, a.name COLLATE NOCASE ASC",
        "albums_desc": " ORDER BY album_count DESC, a.name COLLATE NOCASE ASC",
        "tracks_asc": " ORDER BY track_count ASC, a.name COLLATE NOCASE ASC",
        "tracks_desc": " ORDER BY track_count DESC, a.name COLLATE NOCASE ASC",
        "avg_asc": " ORDER BY avg_rating ASC NULLS LAST, a.name COLLATE NOCASE ASC",
        "avg_desc": " ORDER BY avg_rating DESC NULLS LAST, a.name COLLATE NOCASE ASC"
    }
    query += sort_clauses.get(sort_by, " ORDER BY a.name COLLATE NOCASE ASC")
    query += " LIMIT ? OFFSET ?"
    params = list(where_params)
    params.extend([page_size, offset])

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(query, params)
        rows = [dict(r) for r in cur.fetchall()]

        # Total count for pagination
        count_query = f"SELECT COUNT(*) FROM artists a WHERE 1=1{where_clause}"
        cur.execute(count_query, where_params)
        total = cur.fetchone()[0]

    return {
        "items": rows,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size
    }
    
def get_csv_export_data() -> Tuple[Dict[int, List[str]], List[Dict[str, Any]]]:
    """Fetches artist roster maps and flattened track rows for complete CSV catalog export.

    Pre-aggregates former group names with 'Ex-' prefixes for soloists and
    prepends asterisks ('*') to former group members.

    Returns:
        Tuple[Dict[int, List[str]], List[Dict[str, Any]]]:
            - roster_map: Mapping of artist_id to list of formatted member name strings.
            - rows: List of row dictionaries ordered by artist, release date, and track number.
    """
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT 
                group_id, 
                member_name, 
                is_former 
            FROM artist_members 
            ORDER BY group_id, is_former ASC, display_order ASC, member_name ASC
        """)
        roster_map = {}
        for row in cur.fetchall():
            gid = row["group_id"]
            name = f"*{row['member_name']}" if row["is_former"] else row["member_name"]
            roster_map.setdefault(gid, []).append(name)

        cur.execute("""
            SELECT 
                ar.id AS artist_id,
                ar.name AS artist,
                COALESCE(ar.artist_type, 'group') AS artist_type,
                ar.generation,
                ar.debut_year,
                COALESCE(ar.status, 'Active') AS artist_status,
                pg_sub.parent_group_name,
                al.id AS album_id,
                al.title AS album_title,
                al.album_type,
                al.release_date,
                al.mb_release_group_id,
                t.track_number,
                t.title AS track_title,
                t.rating,
                t.is_title_track,
                COALESCE(t.track_type, 'core') AS track_type,
                t.notes
            FROM tracks t
            JOIN albums al ON t.album_id = al.id
            JOIN artists ar ON al.artist_id = ar.id
            LEFT JOIN (
                SELECT 
                    am.solo_artist_id,
                    GROUP_CONCAT(
                        CASE WHEN am.is_former = 1 THEN 'Ex-' || pg.name ELSE pg.name END,
                        ', '
                    ) AS parent_group_name
                FROM artist_members am
                JOIN artists pg ON am.group_id = pg.id
                GROUP BY am.solo_artist_id
            ) pg_sub ON ar.id = pg_sub.solo_artist_id
            ORDER BY ar.name COLLATE NOCASE ASC, al.release_date ASC, t.track_number ASC
        """)
        rows = [dict(r) for r in cur.fetchall()]
    return roster_map, rows


def get_full_catalog_json_data() -> Dict[str, Any]:
    """Extracts all database tables into a structured catalog dictionary for JSON export.

    Returns:
        Dict[str, Any]: Complete database dump containing metadata, artists,
            albums, tracks, artist_members, and artist_watchlist.
    """
    from datetime import datetime, timezone

    with get_connection() as conn:
        cur = conn.cursor()

        cur.execute("SELECT * FROM artists ORDER BY name COLLATE NOCASE ASC")
        artists = [dict(r) for r in cur.fetchall()]

        cur.execute("SELECT * FROM albums ORDER BY artist_id ASC, release_date ASC")
        albums = [dict(r) for r in cur.fetchall()]

        cur.execute("SELECT * FROM tracks ORDER BY album_id ASC, track_number ASC")
        tracks = [dict(r) for r in cur.fetchall()]

        cur.execute("SELECT * FROM artist_members ORDER BY group_id ASC, display_order ASC")
        artist_members = [dict(r) for r in cur.fetchall()]

        cur.execute("SELECT * FROM artist_watchlist ORDER BY date_added DESC, id DESC")
        watchlist = [dict(r) for r in cur.fetchall()]

    return {
        "metadata": {
            "app": "K-Pop Discography Tracker",
            "version": "2.0",
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "counts": {
                "artists": len(artists),
                "albums": len(albums),
                "tracks": len(tracks),
                "artist_members": len(artist_members),
                "artist_watchlist": len(watchlist)
            }
        },
        "artists": artists,
        "albums": albums,
        "tracks": tracks,
        "artist_members": artist_members,
        "artist_watchlist": watchlist
    }


_RESTORE_TABLE_SPECS = (
    ("artists", ("id", "name", "debut_year", "generation", "status", "artist_type", "parent_groups"), ("name",)),
    ("albums", ("id", "artist_id", "title", "release_date", "album_type", "mb_release_group_id"), ("artist_id", "title")),
    ("tracks", ("id", "album_id", "track_number", "title", "rating", "is_title_track", "track_type", "notes"), ("album_id", "title")),
    ("artist_members", ("id", "group_id", "member_name", "solo_artist_id", "display_order", "is_former"), ("group_id", "member_name")),
    ("artist_watchlist", ("id", "name", "artist_type", "generation", "status", "parent_groups", "members", "predicted_score", "predicted_hits", "predicted_total", "predicted_tracks_json", "predicted_at", "date_added", "notes"), ("name",)),
)


def restore_catalog_from_json(payload: Dict[str, Any]) -> Dict[str, int]:
    """Atomically wipes and repopulates all 5 catalog tables from a JSON backup dump.

    Preserves original row primary keys (including `tracks.id`) so SonicTaste's
    cached audio embeddings remain aligned, verifies foreign keys before commit,
    and synchronizes `sqlite_sequence` for subsequent AUTOINCREMENT inserts.

    Args:
        payload: Parsed dictionary matching `get_full_catalog_json_data()` output.

    Returns:
        Dict[str, int]: Row counts restored per table.

    Raises:
        ValueError: If required tables/fields are missing or foreign-key checks fail.
    """
    if not isinstance(payload, dict):
        raise ValueError("JSON root must be an object containing catalog tables.")

    validated_rows: Dict[str, List[Dict[str, Any]]] = {}
    for table_name, _cols, required_fields in _RESTORE_TABLE_SPECS:
        rows = payload.get(table_name)
        if not isinstance(rows, list):
            raise ValueError(f"Missing or invalid '{table_name}' array in JSON backup.")
        for idx, row in enumerate(rows):
            if not isinstance(row, dict):
                raise ValueError(f"Invalid row at '{table_name}[{idx}]': expected object.")
            for req in required_fields:
                if row.get(req) is None or (isinstance(row.get(req), str) and not row.get(req).strip()):
                    raise ValueError(f"Missing required field '{req}' at '{table_name}[{idx}]'.")
        validated_rows[table_name] = rows

    with get_connection() as conn:
        conn.execute("PRAGMA foreign_keys = OFF;")
        try:
            conn.execute("BEGIN IMMEDIATE;")
            cur = conn.cursor()

            # Wipe in reverse dependency order
            for table_name in ("tracks", "albums", "artist_members", "artist_watchlist", "artists"):
                cur.execute(f"DELETE FROM {table_name}")

            counts: Dict[str, int] = {}
            for table_name, cols, _req in _RESTORE_TABLE_SPECS:
                rows = validated_rows[table_name]
                if rows:
                    col_list = ", ".join(cols)
                    placeholders = ", ".join("?" for _ in cols)
                    sql = f"INSERT INTO {table_name} ({col_list}) VALUES ({placeholders})"
                    cur.executemany(sql, [tuple(row.get(c) for c in cols) for row in rows])
                counts[table_name] = len(rows)

            # Sync AUTOINCREMENT sequences so future inserts never collide with preserved IDs
            has_seq = cur.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'"
            ).fetchone()
            if has_seq:
                for table_name, _cols, _req in _RESTORE_TABLE_SPECS:
                    cur.execute("DELETE FROM sqlite_sequence WHERE name = ?", (table_name,))
                    cur.execute(
                        f"INSERT INTO sqlite_sequence (name, seq) SELECT ?, COALESCE(MAX(id), 0) FROM {table_name}",
                        (table_name,),
                    )

            fk_errors = cur.execute("PRAGMA foreign_key_check;").fetchall()
            if fk_errors:
                raise ValueError(f"Foreign key integrity check failed ({len(fk_errors)} violation(s)).")

            conn.commit()
            return counts
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.execute("PRAGMA foreign_keys = ON;")


def restore_catalog_from_db(source_db_path: Union[str, Path]) -> Dict[str, int]:
    """Validates a SQLite .db backup and atomically copies it into the live database.

    Uses SQLite's online `Connection.backup()` API so WAL-mode locks on Windows
    are handled safely, preserving all original row IDs and schema indexes.

    Args:
        source_db_path: Path to the uploaded SQLite database file.

    Returns:
        Dict[str, int]: Row counts present in the restored database per table.

    Raises:
        ValueError: If the file fails `PRAGMA integrity_check` or lacks required tables.
    """
    src_path = Path(source_db_path).resolve()
    if not src_path.exists():
        raise ValueError("Uploaded database file could not be found.")

    src_conn = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
    try:
        integrity_row = src_conn.execute("PRAGMA integrity_check;").fetchone()
        if not integrity_row or str(integrity_row[0]).lower() != "ok":
            raise ValueError(f"Database integrity check failed: {integrity_row[0] if integrity_row else 'unknown'}")

        existing_tables = {
            row[0]
            for row in src_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        required_tables = [spec[0] for spec in _RESTORE_TABLE_SPECS]
        missing = [t for t in required_tables if t not in existing_tables]
        if missing:
            raise ValueError(f"Database is missing required table(s): {', '.join(missing)}")

        counts = {
            table_name: int(src_conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0])
            for table_name in required_tables
        }

        with get_connection() as dest_conn:
            src_conn.backup(dest_conn)
    finally:
        src_conn.close()

    checkpoint_wal()
    return counts


def get_existing_artist_albums(artist_name: str) -> List[Dict[str, Any]]:
    """Returns cataloged albums for an artist to enable discography deduplication.

    Used by bulk queue staging to mark already cataloged releases (`in_library`).

    Args:
        artist_name: Artist name string.

    Returns:
        List[Dict[str, Any]]: List of album dicts with keys id, title, album_type, mb_release_group_id.
    """
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT 
                al.id,
                al.title,
                al.album_type,
                al.mb_release_group_id
            FROM albums al
            JOIN artists ar ON al.artist_id = ar.id
            WHERE LOWER(ar.name) = LOWER(?)
        """, (artist_name.strip(),))
        return [dict(r) for r in cur.fetchall()]


def get_existing_track_defaults(artist_name: str) -> Tuple[set, Dict[str, float]]:
    """Returns prior substantive title-track titles and most recent ratings for an artist.

    Uses Unicode-aware `py_lower()` matching and excludes `alt_lang` / `non_canon` tracks.
    """
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT py_lower(TRIM(t.title)) AS norm_title, t.is_title_track, t.rating
            FROM tracks t
            JOIN albums a ON t.album_id = a.id
            JOIN artists ar ON a.artist_id = ar.id
            WHERE py_lower(ar.name) = py_lower(?)
              AND COALESCE(t.track_type, 'core') NOT IN ('alt_lang', 'non_canon')
              AND (t.is_title_track = 1 OR t.rating IS NOT NULL)
            ORDER BY COALESCE(a.release_date, '') ASC, a.id ASC, t.id ASC
            """,
            (artist_name.strip(),),
        )
        prior_titles = set()
        prior_ratings: Dict[str, float] = {}
        for row in cur.fetchall():
            norm = row["norm_title"]
            if row["is_title_track"]:
                prior_titles.add(norm)
            if row["rating"] is not None:
                prior_ratings[norm] = float(row["rating"])
        return prior_titles, prior_ratings


def get_existing_title_track_titles(artist_name: str) -> set:
    """Returns lowercase normalized titles of tracks previously cataloged as title tracks."""
    prior_titles, _ = get_existing_track_defaults(artist_name)
    return prior_titles


def get_existing_track_ratings(artist_name: str) -> Dict[str, float]:
    """Returns a mapping of lowercase normalized track titles to their most recent prior rating."""
    _, prior_ratings = get_existing_track_defaults(artist_name)
    return prior_ratings


# ===========================================================================
# 5. PROSPECTIVE ARTIST WATCHLIST (DISCOVERY QUEUE)
# ===========================================================================

def graduate_watchlist_artist(conn: sqlite3.Connection, artist_name: str) -> bool:
    """Promotes a prospective artist from the watchlist to the active library on album commit.

    Carries over prospective generation, status, and artist_type attributes if not
    already populated on the active artist record. If graduating a solo artist with
    parent group strings, syncs soloist affiliations into `artist_members`.

    Args:
        conn: Active SQLite connection.
        artist_name: Case-insensitive artist name string.

    Returns:
        bool: True if an artist was found on the watchlist and graduated, False otherwise.
    """
    cur = conn.cursor()
    cur.execute("SELECT * FROM artist_watchlist WHERE LOWER(name) = LOWER(?)", (artist_name.strip(),))
    row = cur.fetchone()
    if not row:
        return False
    
    data = dict(row)
    cur.execute("DELETE FROM artist_watchlist WHERE id = ?", (data["id"],))

    # Carry over generation, status, and artist_type if not set on the active artist
    cur.execute("SELECT id, generation, status, artist_type FROM artists WHERE LOWER(name) = LOWER(?)", (artist_name.strip(),))
    ar_row = cur.fetchone()
    if ar_row:
        artist_id = ar_row["id"]
        cur.execute("""
            UPDATE artists
            SET generation = COALESCE(?, generation),
                status = COALESCE(?, status),
                artist_type = COALESCE(?, artist_type)
            WHERE id = ?
        """, (
            data.get("generation"),
            data.get("status"),
            data.get("artist_type"),
            artist_id
        ))

        # If solo artist with parent_groups, connect affiliations
        effective_type = data.get("artist_type") or ar_row["artist_type"]
        if effective_type == "solo" and data.get("parent_groups"):
            sync_soloist_affiliations(conn, artist_id, artist_name.strip(), data["parent_groups"])
        elif effective_type == "group" and data.get("members"):
            member_list = [m.strip() for m in data["members"].split(",") if m.strip()]
            if member_list:
                sync_artist_members(conn, artist_id, member_list)

    return True


def get_watchlist_artists(artist_type: Optional[str] = None) -> List[Dict[str, Any]]:
    """Retrieves all queued prospective artists from the watchlist.

    Results are ordered by predicted_score descending (unscored last), then date added.

    Args:
        artist_type: Optional filter by 'group' or 'solo'.

    Returns:
        List[Dict[str, Any]]: List of watchlist entries.
    """
    with get_connection() as conn:
        cur = conn.cursor()
        query = """
            SELECT id, name, artist_type, generation, status, parent_groups, members,
                   predicted_score, predicted_hits, predicted_total, predicted_tracks_json,
                   predicted_at, date_added, notes
            FROM artist_watchlist
            WHERE 1=1
        """
        params = []
        if artist_type:
            query += " AND LOWER(artist_type) = LOWER(?)"
            params.append(artist_type.strip())

        query += """
            ORDER BY
                predicted_score IS NULL ASC,
                predicted_score DESC,
                date_added DESC,
                id DESC
        """
        cur.execute(query, params)
        return [dict(r) for r in cur.fetchall()]


def add_watchlist_artist(payload: Dict[str, Any]) -> int:
    """Inserts a prospective artist into the watchlist queue."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO artist_watchlist (name, artist_type, generation, status, parent_groups, members, date_added, notes)
            VALUES (?, ?, ?, ?, ?, ?, COALESCE(?, date('now')), ?)
        """, (
            payload["name"].strip(),
            payload.get("artist_type", "group"),
            payload.get("generation"),
            payload.get("status", "Active"),
            payload.get("parent_groups"),
            payload.get("members"),
            payload.get("date_added"),
            payload.get("notes")
        ))
        conn.commit()
        return cur.lastrowid


def update_watchlist_artist(watchlist_id: int, payload: Dict[str, Any]) -> bool:
    """Updates metadata and notes on an existing watchlist prospect."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            UPDATE artist_watchlist
            SET name = ?, artist_type = ?, generation = ?, status = ?, parent_groups = ?, members = ?, notes = ?
            WHERE id = ?
        """, (
            payload["name"].strip(),
            payload.get("artist_type", "group"),
            payload.get("generation"),
            payload.get("status", "Active"),
            payload.get("parent_groups"),
            payload.get("members"),
            payload.get("notes"),
            watchlist_id
        ))
        conn.commit()
        return cur.rowcount > 0


def update_watchlist_prediction(
    watchlist_id: int,
    predicted_score: float,
    predicted_hits: int,
    predicted_total: int,
    predicted_tracks_json: str,
) -> bool:
    """Stores SonicTaste (STC) prediction results on an existing watchlist entry."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            UPDATE artist_watchlist
            SET predicted_score = ?, predicted_hits = ?, predicted_total = ?, predicted_tracks_json = ?,
                predicted_at = date('now')
            WHERE id = ?
        """, (predicted_score, predicted_hits, predicted_total, predicted_tracks_json, watchlist_id))
        conn.commit()
        return cur.rowcount > 0


def delete_watchlist_artist(watchlist_id: int) -> bool:
    """Deletes an artist from the watchlist queue.

    Args:
        watchlist_id: Primary key ID of the watchlist entry.

    Returns:
        bool: True if entry was deleted, False if ID not found.
    """
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM artist_watchlist WHERE id = ?", (watchlist_id,))
        conn.commit()
        return cur.rowcount > 0


def get_all_library_albums() -> List[Dict[str, Any]]:
    """Returns all library albums with track counts and aggregated average rating.

    Returns:
        List[Dict[str, Any]]: Album dictionaries sorted descending by release date.
    """
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT 
                al.id AS album_id,
                ar.name AS artist,
                al.title AS album_title,
                al.release_date,
                al.album_type,
                COUNT(t.id) AS track_count,
                ROUND(AVG(t.rating), 2) AS avg_rating
            FROM albums al
            JOIN artists ar ON al.artist_id = ar.id
            LEFT JOIN tracks t ON t.album_id = al.id
            GROUP BY al.id
            ORDER BY al.release_date DESC
        """)
        return [dict(r) for r in cur.fetchall()]


def get_filtered_tracks(
    artist: Optional[str] = None,
    min_rating: Optional[float] = None,
    title_only: Optional[bool] = False,
    search: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Returns tracks joined with album and artist metadata matching query criteria.

    Args:
        artist: Optional exact artist name filter.
        min_rating: Optional minimum rating cutoff (e.g., 4.0).
        title_only: If True, only includes tracks flagged as is_title_track.
        search: Optional case-insensitive search across title, album, artist, and notes.

    Returns:
        List[Dict[str, Any]]: Track records ordered by release date descending, then track_number.
    """
    with get_connection() as conn:
        cur = conn.cursor()
        query = """
            SELECT 
                t.id AS track_id,
                t.track_number,
                t.title AS track_title,
                t.rating,
                t.is_title_track,
                t.track_type,
                t.notes,
                al.id AS album_id,
                al.title AS album_title,
                al.release_date,
                al.album_type,
                ar.id AS artist_id,
                ar.name AS artist_name
            FROM tracks t
            JOIN albums al ON t.album_id = al.id
            JOIN artists ar ON al.artist_id = ar.id
            WHERE 1=1
        """
        params = []
        if artist:
            query += " AND LOWER(ar.name) = LOWER(?)"
            params.append(artist.strip())
        if min_rating is not None:
            query += " AND t.rating >= ?"
            params.append(min_rating)
        if title_only:
            query += " AND t.is_title_track = 1"
        if search:
            query += """ AND (
                LOWER(t.title) LIKE ? 
                OR LOWER(al.title) LIKE ? 
                OR LOWER(ar.name) LIKE ? 
                OR LOWER(IFNULL(t.notes, '')) LIKE ?
            )"""
            term = f"%{search.lower()}%"
            params.extend([term, term, term, term])

        query += " ORDER BY al.release_date DESC, t.track_number ASC"
        cur.execute(query, params)
        return [dict(r) for r in cur.fetchall()]


def _fetch_solo_parent_map(cur: sqlite3.Cursor) -> Dict[int, List[Dict[str, Any]]]:
    """Preloads linked and unlinked parent group affiliations for all solo artists in bulk."""
    cur.execute("""
        SELECT 
            m.solo_artist_id,
            g.id AS group_id, 
            g.name AS group_name,
            m.is_former
        FROM artist_members m
        JOIN artists g ON m.group_id = g.id
        WHERE m.solo_artist_id IS NOT NULL
        ORDER BY m.is_former ASC, g.debut_year ASC, g.name COLLATE NOCASE ASC
    """)
    linked_map: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for r in cur.fetchall():
        item = dict(r)
        sid = item.pop("solo_artist_id")
        item["is_former"] = bool(item.get("is_former"))
        linked_map[sid].append(item)

    cur.execute("""
        SELECT id, parent_groups
        FROM artists
        WHERE artist_type = 'solo' AND parent_groups IS NOT NULL AND TRIM(parent_groups) != ''
    """)
    raw_map = {r["id"]: r["parent_groups"] for r in cur.fetchall()}

    solo_parent_map: Dict[int, List[Dict[str, Any]]] = {}
    for sid in set(linked_map.keys()) | set(raw_map.keys()):
        solo_parent_map[sid] = _merge_unlinked_parent_groups(linked_map.get(sid, []), raw_map.get(sid))
    return solo_parent_map


def get_all_artist_profiles() -> List[Dict[str, Any]]:
    """Returns all artist profiles preloaded with group members and soloist affiliations.

    Executes 3 bulk queries to eliminate N+1 overhead:
    1. Base artist profile list and album counts.
    2. Bulk map of group rosters from `artist_members`.
    3. Bulk map of soloist parent groups from `artist_members`.

    Returns:
        List[Dict[str, Any]]: List of artist records with attached 'members' or 'parent_groups'.
    """
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT 
                ar.id, 
                ar.name, 
                ar.debut_year,
                ar.generation,
                COALESCE(ar.status, 'Active') AS status,
                COALESCE(ar.artist_type, 'group') AS artist_type,
                COUNT(al.id) AS album_count
            FROM artists ar
            LEFT JOIN albums al ON al.artist_id = ar.id
            GROUP BY ar.id
            ORDER BY ar.name COLLATE NOCASE ASC
        """)
        artists = [dict(r) for r in cur.fetchall()]
        
        # Preload group member rosters in 1 query instead of N queries
        cur.execute("""
            SELECT 
                m.id,
                m.group_id,
                m.member_name,
                m.solo_artist_id,
                m.is_former,
                s.name AS solo_artist_name
            FROM artist_members m
            LEFT JOIN artists s ON m.solo_artist_id = s.id
            ORDER BY m.is_former ASC, m.display_order ASC, m.member_name ASC
        """)
        group_members_map = defaultdict(list)
        for r in cur.fetchall():
            item = dict(r)
            gid = item.pop("group_id")
            group_members_map[gid].append(item)

        # Preload soloist parent groups in 1 query instead of N queries
        solo_parent_map = _fetch_solo_parent_map(cur)

        for a in artists:
            if a["artist_type"] == "group":
                members = group_members_map.get(a["id"], [])
                a["members"] = [m["member_name"] for m in members]
                a["member_objects"] = members
            else:
                a["members"] = []
                a["parent_groups"] = solo_parent_map.get(a["id"], [])
        return artists


def create_artist_profile(payload: Dict[str, Any]) -> int:
    """Creates a new artist profile with metadata, group member rosters, or soloist affiliations.

    Args:
        payload: Dictionary containing 'name', and optional 'debut_year', 'generation',
            'status', 'artist_type', 'members', and 'parent_groups'.

    Returns:
        int: Primary key ID of the created artist.
    """
    name = payload["name"].strip()
    debut_year = payload.get("debut_year")
    generation = payload.get("generation")
    status = payload.get("status", "Active")
    artist_type = payload.get("artist_type", "group")
    members = payload.get("members")

    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO artists (name, debut_year, generation, status, artist_type)
            VALUES (?, ?, ?, ?, ?)
        """, (name, debut_year, generation, status, artist_type))
        artist_id = cur.lastrowid

        if artist_type == "group":
            if members is not None:
                sync_artist_members(conn, artist_id, members)
            _auto_link_soloists_for_group(conn, artist_id, name)
        elif artist_type == "solo":
            parent_groups_str = payload.get("parent_groups") or ""
            sync_soloist_affiliations(conn, artist_id, name, parent_groups_str)

        # Graduate / remove from watchlist if present
        cur.execute("DELETE FROM artist_watchlist WHERE LOWER(name) = LOWER(?)", (name,))
        conn.commit()
    return artist_id


def update_artist_profile(artist_id: int, payload: Dict[str, Any]):
    """Updates artist metadata, group member rosters, or soloist affiliations.

    If the artist is a group and 'members' is supplied, synchronizes the group roster.
    If the artist is a soloist and 'parent_groups' is supplied, synchronizes parent group links.

    Args:
        artist_id: Primary key ID of the artist.
        payload: Dictionary containing 'name', and optional 'debut_year', 'generation',
            'status', 'artist_type', 'members', and 'parent_groups'.
    """
    name = payload["name"].strip()
    debut_year = payload.get("debut_year")
    generation = payload.get("generation")
    status = payload.get("status", "Active")
    artist_type = payload.get("artist_type", "group")
    members = payload.get("members")

    with get_connection() as conn:
        conn.execute("""
            UPDATE artists 
            SET name = ?, debut_year = ?, generation = ?, status = ?, artist_type = ?
            WHERE id = ?
        """, (name, debut_year, generation, status, artist_type, artist_id))

        if artist_type == "group":
            # Clear any stale soloist links if artist was converted from solo to group
            conn.execute("UPDATE artist_members SET solo_artist_id = NULL WHERE solo_artist_id = ?", (artist_id,))
            if members is not None:
                sync_artist_members(conn, artist_id, members)
            _auto_link_soloists_for_group(conn, artist_id, name)
        elif artist_type == "solo":
            # Clear any stale group roster rows if artist was converted from group to solo
            conn.execute("DELETE FROM artist_members WHERE group_id = ?", (artist_id,))
            parent_groups_str = payload.get("parent_groups") or ""
            sync_soloist_affiliations(conn, artist_id, name, parent_groups_str)
        conn.commit()


def delete_artist_profile(artist_id: int):
    """Deletes an artist and cascades deletion of albums, tracks, and member rosters.

    Nullifies `solo_artist_id` references in `artist_members` to maintain group roster records.

    Args:
        artist_id: Primary key ID of the artist.
    """
    with get_connection() as conn:
        conn.execute("DELETE FROM artists WHERE id = ?", (artist_id,))
        conn.commit()


def get_artists_with_catalog_ratings() -> List[str]:
    """Returns alphabetical list of artist names who have at least one cataloged album.

    Returns:
        List[str]: Artist names.
    """
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT ar.name
            FROM artists ar
            JOIN albums al ON al.artist_id = ar.id
            GROUP BY ar.id
            HAVING COUNT(al.id) > 0
            ORDER BY ar.name COLLATE NOCASE ASC
        """)
        return [r["name"] for r in cur.fetchall()]


def update_single_track_rating(track_id: int, rating: Optional[float]) -> bool:
    """Updates only the rating of an individual track.

    Args:
        track_id: Primary key ID of the track.
        rating: New rating value (1.0 - 5.0) or None to unrate.

    Returns:
        bool: True if track was found and updated, False otherwise.
    """
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE tracks SET rating = ? WHERE id = ?", (rating, track_id))
        conn.commit()
        return cur.rowcount > 0


# ===========================================================================
# 6. ANALYTICS, Q-WAR ENGINE & DISCOGRAPHY TRAJECTORY
# ===========================================================================

# CANONICAL TRACK INVARIANT (CANONICAL_UNIQUE_TRACK_SQL):
# All discography-level statistics (total catalog tracks, overall score, hit rate,
# rating distribution histogram, and Q-WAR metrics) MUST filter tracks with this condition.
#
# It enforces four core rules:
# 1. Non-canon tracks (skits, instrumentals, intros, outros, remixes) are excluded.
# 2. Priority 1 (Rating Protection): A rated track instance is NEVER shadowed by an unrated duplicate.
# 3. Priority 2 (Domestic Preference): When duplicate tracks exist across a domestic release and
#    a localized alternate-language edition (e.g., '-JP ver.-', '(English version)'), the domestic
#    release always takes canonical precedence. (Unique original B-sides on localized albums remain eligible.)
# 4. Priority 3 (Era Pre-Release Gate & Release Scale): When rating and localized status are tied:
#    - Singles released within 120 days before an EP/LP (or on/after) are shadowed by the parent album.
#    - Standalone singles released >120 days before an EP/LP shadow the later album copy, preserving original era milestones.
#    - Other ties prefer the release with the larger substantive track count (tiebreak by album ID).
CANONICAL_UNIQUE_TRACK_SQL = """
    COALESCE(tr.track_type, 'core') NOT IN ('non_canon', 'alt_lang')
    AND NOT EXISTS (
        SELECT 1
        FROM tracks t2
        JOIN albums a2 ON t2.album_id = a2.id
        WHERE a2.artist_id = al.artist_id
          AND a2.id != al.id
          AND REPLACE(REPLACE(LOWER(TRIM(t2.title)), '’', ''''), '‘', '''') = REPLACE(REPLACE(LOWER(TRIM(tr.title)), '’', ''''), '‘', '''')
          AND COALESCE(t2.track_type, 'core') = COALESCE(tr.track_type, 'core')
          AND (
              -- Priority 1: A rated copy always shadows an unrated copy
              (t2.rating IS NOT NULL AND tr.rating IS NULL)
              OR (
                  ((t2.rating IS NOT NULL) = (tr.rating IS NOT NULL))
                  AND (
                      -- Priority 2: A domestic release always shadows an alt-language edition release
                      (is_alt_lang(al.title) = 1 AND is_alt_lang(a2.title) = 0)
                      OR (
                          (is_alt_lang(al.title) = is_alt_lang(a2.title))
                          AND (
                              -- Priority 3: Single vs EP/LP with 120-day pre-release window
                              CASE 
                                  WHEN al.album_type = 'Single' AND a2.album_type IN ('EP', 'LP') THEN
                                      (release_day_gap(a2.release_date, al.release_date) BETWEEN 0 AND 120
                                       OR release_day_gap(al.release_date, a2.release_date) >= 0)
                                  WHEN al.album_type IN ('EP', 'LP') AND a2.album_type = 'Single' THEN
                                      release_day_gap(al.release_date, a2.release_date) > 120
                                  ELSE
                                      ((SELECT COUNT(*) FROM tracks t_cnt2 WHERE t_cnt2.album_id = a2.id AND COALESCE(t_cnt2.track_type, 'core') NOT IN ('non_canon', 'alt_lang')) >
                                       (SELECT COUNT(*) FROM tracks t_cnt1 WHERE t_cnt1.album_id = al.id AND COALESCE(t_cnt1.track_type, 'core') NOT IN ('non_canon', 'alt_lang'))
                                       OR (
                                           (SELECT COUNT(*) FROM tracks t_cnt2 WHERE t_cnt2.album_id = a2.id AND COALESCE(t_cnt2.track_type, 'core') NOT IN ('non_canon', 'alt_lang')) =
                                           (SELECT COUNT(*) FROM tracks t_cnt1 WHERE t_cnt1.album_id = al.id AND COALESCE(t_cnt1.track_type, 'core') NOT IN ('non_canon', 'alt_lang'))
                                           AND a2.id > al.id
                                       ))
                              END
                          )
                      )
                  )
              )
          )
    )
"""

def _match_member_in_track(member_names: List[str], title: Optional[str], notes: Optional[str]) -> bool:
    """Returns True if any member name appears as a whole word (Unicode case-insensitive) in title or notes."""
    haystack = f"{title or ''} {notes or ''}".casefold()
    if not haystack.strip():
        return False
    for name in member_names:
        clean = (name or "").strip().casefold()
        if not clean:
            continue
        if re.search(rf"(?<!\w){re.escape(clean)}(?!\w)", haystack):
            return True
    return False


def _fetch_soloist_group_tracks(
    conn: sqlite3.Connection,
    soloist_ids: Optional[List[int]] = None,
    own_titles_by_soloist: Optional[Dict[int, set]] = None,
) -> Dict[int, List[Dict[str, Any]]]:
    """Discovers canonical solo/unit tracks on parent group albums matching each soloist's name.

    Scans releases of each soloist's registered parent group(s) in `artist_members` for tracks
    categorized as 'solo' or 'unit' under `CANONICAL_UNIQUE_TRACK_SQL` where the soloist's name
    appears in the track title or notes. Excludes titles already present in the soloist's own
    canonical catalog to prevent double-counting.
    """
    cur = conn.cursor()
    params: List[Any] = []
    filter_sql = "WHERE am.solo_artist_id IS NOT NULL"
    if soloist_ids is not None:
        if not soloist_ids:
            return {}
        placeholders = ",".join("?" for _ in soloist_ids)
        filter_sql = f"WHERE am.solo_artist_id IN ({placeholders})"
        params.extend(soloist_ids)

    cur.execute(f"""
        SELECT am.solo_artist_id, am.group_id, am.member_name, s.name AS soloist_name
        FROM artist_members am
        JOIN artists s ON am.solo_artist_id = s.id
        {filter_sql}
    """, params)

    parent_groups_by_solo: Dict[int, set] = defaultdict(set)
    names_by_solo: Dict[int, set] = defaultdict(set)
    all_group_ids: set = set()

    for row in cur.fetchall():
        sid = row["solo_artist_id"]
        gid = row["group_id"]
        parent_groups_by_solo[sid].add(gid)
        all_group_ids.add(gid)
        if row["soloist_name"]:
            names_by_solo[sid].add(row["soloist_name"])
        if row["member_name"]:
            names_by_solo[sid].add(row["member_name"])

    if not all_group_ids:
        return {}

    grp_placeholders = ",".join("?" for _ in all_group_ids)
    cur.execute(f"""
        SELECT
            al.artist_id AS group_id,
            tr.id,
            tr.album_id,
            tr.title,
            tr.notes,
            tr.rating,
            COALESCE(tr.track_type, 'core') AS track_type,
            tr.is_title_track,
            al.release_date
        FROM tracks tr
        JOIN albums al ON tr.album_id = al.id
        WHERE al.artist_id IN ({grp_placeholders})
          AND COALESCE(tr.track_type, 'core') IN ('solo', 'unit')
          AND {CANONICAL_UNIQUE_TRACK_SQL}
    """, list(all_group_ids))

    tracks_by_group: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for row in cur.fetchall():
        tracks_by_group[row["group_id"]].append(dict(row))

    discovered: Dict[int, List[Dict[str, Any]]] = {}
    own_map = own_titles_by_soloist or {}

    for sid, gids in parent_groups_by_solo.items():
        match_names = list(names_by_solo.get(sid, ()))
        own_titles = own_map.get(sid, set())
        seen_ids = set()
        matched: List[Dict[str, Any]] = []
        for gid in gids:
            for tr in tracks_by_group.get(gid, ()):
                if tr["id"] in seen_ids:
                    continue
                norm_title = (tr.get("title") or "").strip().casefold()
                if norm_title and norm_title in own_titles:
                    continue
                if _match_member_in_track(match_names, tr.get("title"), tr.get("notes")):
                    seen_ids.add(tr["id"])
                    matched.append(tr)
        if matched:
            discovered[sid] = matched

    return discovered


def get_artist_summary_metrics(artist: str) -> Dict[str, Any]:
    """Computes career metrics, trajectory data points, and standout releases for single-artist deep dive.

    Performs full discography analysis:
    1. Identifies advance singles and chronological release sequence for chart plotting.
    2. Calculates running cumulative Q-WAR and volume progression per release.
    3. Computes career scores, hit rate, and perfect-5 percentages under CANONICAL_UNIQUE_TRACK_SQL.
    4. Evaluates Title Tracks vs. B-Sides performance and 5-bucket rating histogram.
    5. Calculates career duration and productivity pacing (tracks per year).
    6. Identifies top-rated EP/LP standout releases.

    Args:
        artist: Case-insensitive artist name string.

    Returns:
        Dict[str, Any]: Comprehensive summary metrics dictionary containing album trend points,
            Q-WAR scores, productivity pacing, roster/parent affiliations, and histogram data.
    """
    with get_connection() as conn:
        cur = conn.cursor()

        # 0. Resolve artist metadata & ID
        cur.execute("""
            SELECT id, name, debut_year, generation, COALESCE(status, 'Active') AS status, COALESCE(artist_type, 'group') AS artist_type 
            FROM artists 
            WHERE py_lower(name) = py_lower(?)
        """, (artist,))
        artist_meta = dict(cur.fetchone() or {"id": None, "debut_year": None, "generation": None, "status": "Active", "artist_type": "group"})
        artist_id = artist_meta["id"]

        # Raw catalog totals (all releases and tracks in DB for this artist)
        cur.execute("""
            SELECT 
                COUNT(DISTINCT al.id) AS raw_total_albums,
                COUNT(tr.id) AS raw_total_tracks
            FROM albums al
            LEFT JOIN tracks tr ON tr.album_id = al.id
            WHERE al.artist_id = ?
        """, (artist_id,))
        raw_row = cur.fetchone()
        raw_total_albums = raw_row["raw_total_albums"] if raw_row else 0
        raw_total_tracks = raw_row["raw_total_tracks"] if raw_row else 0

        # 1. Fetch release points & identify duplicate/offshoot singles or localized repackages
        cur.execute("""
            SELECT 
                al.id AS album_id,
                al.title AS album_title,
                al.release_date,
                al.album_type,
                ROUND(AVG(tr.rating), 2) AS avg_score,
                COUNT(tr.id) AS track_count,
                COUNT(CASE WHEN tr.rating IS NOT NULL THEN 1 END) AS rated_track_count,
                EXISTS (
                    SELECT 1
                    FROM tracks t1
                    JOIN albums a1 ON t1.album_id = a1.id
                    JOIN tracks t2 ON LOWER(TRIM(t1.title)) = LOWER(TRIM(t2.title))
                    JOIN albums a2 ON t2.album_id = a2.id
                    WHERE a1.id = al.id
                      AND a2.id != al.id
                      AND a2.artist_id = ?
                      AND (
                          -- Case A: al is a Single Album whose song appears on an EP/LP
                          (
                              al.album_type = 'Single'
                              AND a2.album_type IN ('EP', 'LP')
                              AND (
                                  release_day_gap(a2.release_date, al.release_date) BETWEEN 0 AND 120
                                  OR release_day_gap(al.release_date, a2.release_date) >= 0
                              )
                          )
                          OR
                          -- Case B: Single has fewer rated tracks than another single (and is not an earlier standalone single)
                          (
                              al.album_type = 'Single'
                              AND a2.album_type = 'Single'
                              AND (
                                  release_day_gap(a2.release_date, al.release_date) BETWEEN 0 AND 120
                                  OR release_day_gap(al.release_date, a2.release_date) >= 0
                              )
                              AND (SELECT COUNT(*) FROM tracks WHERE album_id = a1.id AND rating IS NOT NULL) < 
                                  (SELECT COUNT(*) FROM tracks WHERE album_id = a2.id AND rating IS NOT NULL)
                          )
                          OR
                          -- Case C: Alt-language localized edition sharing tracks with domestic release
                          (
                              al.album_type IN ('EP', 'LP')
                              AND a2.album_type IN ('EP', 'LP')
                              AND is_alt_lang(al.title) = 1
                              AND is_alt_lang(a2.title) = 0
                          )
                      )
                ) AS is_advance_single
            FROM albums al
            JOIN tracks tr ON tr.album_id = al.id
            WHERE al.artist_id = ? AND tr.rating IS NOT NULL
            GROUP BY al.id
            ORDER BY al.release_date ASC, al.id ASC
        """, (artist_id, artist_id))

        album_trend = [dict(r) for r in cur.fetchall()]

        # Query all canonical tracks for this artist in a single pass to compute
        # album cumulative stats, career performance metrics, title/b-side splits, and histogram.
        cur.execute(f"""
            SELECT 
                tr.id,
                tr.album_id,
                tr.title,
                tr.rating,
                COALESCE(tr.track_type, 'core') AS track_type,
                tr.is_title_track
            FROM tracks tr
            JOIN albums al ON tr.album_id = al.id
            WHERE al.artist_id = ?
              AND {CANONICAL_UNIQUE_TRACK_SQL}
        """, (artist_id,))
        own_canon_tracks = [dict(r, _is_own=True) for r in cur.fetchall()]

        discovered_group_tracks: List[Dict[str, Any]] = []
        if artist_id and artist_meta.get("artist_type") == "solo":
            own_titles = {(r.get("title") or "").strip().casefold() for r in own_canon_tracks if r.get("title")}
            discovered_group_tracks = [
                dict(r, _is_own=False)
                for r in _fetch_soloist_group_tracks(conn, [artist_id], {artist_id: own_titles}).get(artist_id, [])
            ]
            raw_total_tracks += len(discovered_group_tracks)

        canon_tracks = own_canon_tracks + discovered_group_tracks

        canon_album_stats = defaultdict(lambda: {
            "album_id": None,
            "canon_total_tracks": 0,
            "canon_rated_tracks": 0,
            "rating_sum": 0.0,
            "fives": 0,
            "fours": 0,
            "threes": 0,
            "skips": 0
        })

        total_catalog = len(canon_tracks)
        total_rated = 0
        overall_rating_sum = 0.0
        playable_tracks = 0
        overall_perfect = 0

        group_rated = 0
        group_rating_sum = 0.0
        group_playable = 0
        group_perfect = 0

        fives = 0
        fours = 0
        threes = 0
        skips = 0

        t_cnt = 0
        t_rating_sum = 0.0
        t_play = 0
        t_perfect = 0

        b_cnt = 0
        b_rating_sum = 0.0
        b_play = 0
        b_perfect = 0

        dist_rows = defaultdict(int)

        for row in canon_tracks:
            is_own = row.get("_is_own", True)
            if is_own:
                alb_id = row["album_id"]
                stat = canon_album_stats[alb_id]
                stat["album_id"] = alb_id
                stat["canon_total_tracks"] += 1

            r = row["rating"]
            if r is not None:
                if is_own:
                    stat["canon_rated_tracks"] += 1
                    stat["rating_sum"] += r

                total_rated += 1
                overall_rating_sum += r

                bucket = int(r)
                dist_rows[bucket] += 1

                if r >= 5.0:
                    if is_own:
                        stat["fives"] += 1
                    fives += 1
                    overall_perfect += 1
                elif r >= 4.0:
                    if is_own:
                        stat["fours"] += 1
                    fours += 1
                elif r >= 3.0:
                    if is_own:
                        stat["threes"] += 1
                    threes += 1
                else:
                    if is_own:
                        stat["skips"] += 1
                    skips += 1

                if r >= 3.0:
                    playable_tracks += 1

                ttype = row["track_type"]
                if is_own and ttype == "core":
                    group_rated += 1
                    group_rating_sum += r
                    if r >= 3.0:
                        group_playable += 1
                    if r == 5.0:
                        group_perfect += 1

                is_title = row["is_title_track"]
                if is_title == 1:
                    t_cnt += 1
                    t_rating_sum += r
                    if r >= 3.0:
                        t_play += 1
                    if r == 5.0:
                        t_perfect += 1
                elif ttype in ("core", "solo", "unit"):
                    b_cnt += 1
                    b_rating_sum += r
                    if r >= 3.0:
                        b_play += 1
                    if r == 5.0:
                        b_perfect += 1

        cum_fives = 0
        cum_fours = 0
        cum_threes = 0
        cum_skips = 0
        cum_rated = 0
        cum_rating_sum = 0.0

        for a in album_trend:
            a["is_advance_single"] = bool(a["is_advance_single"])
            c_stat = canon_album_stats.get(a["album_id"], {})
            cum_fives += c_stat.get("fives") or 0
            cum_fours += c_stat.get("fours") or 0
            cum_threes += c_stat.get("threes") or 0
            cum_skips += c_stat.get("skips") or 0
            cum_rated += c_stat.get("canon_rated_tracks") or 0
            cum_rating_sum += c_stat.get("rating_sum") or 0.0

            running_avg = _round_half_up(cum_rating_sum / cum_rated, 2) if cum_rated > 0 else 0.0
            running_qwar = calculate_qwar_metrics(
                fives=cum_fives,
                fours=cum_fours,
                threes=cum_threes,
                skips=cum_skips,
                total_tracks=cum_rated,
                avg_rating=running_avg
            )
            a["cumulative_qwar"] = running_qwar["compacted_qwar"]
            a["cumulative_legacy_qwar"] = running_qwar["legacy_qwar"]
            a["cumulative_qwar_per_track"] = running_qwar["qwar_per_track"]
            a["cumulative_rated_tracks"] = cum_rated
            a["cumulative_avg_score"] = running_avg

        advance_album_ids = [a["album_id"] for a in album_trend if a["is_advance_single"]]
        plotted_album_count = len(album_trend) - len(advance_album_ids)

        # 2. Performance Metrics
        overall_avg = _round_half_up(overall_rating_sum / total_rated, 2) if total_rated > 0 else None
        overall_hit_rate = round((playable_tracks / total_rated * 100), 1) if total_rated > 0 else 0.0
        overall_perfect_pct = round((overall_perfect / total_rated * 100), 1) if total_rated > 0 else 0.0

        group_avg = _round_half_up(group_rating_sum / group_rated, 2) if group_rated > 0 else None
        group_hit_rate = round((group_playable / group_rated * 100), 1) if group_rated > 0 else 0.0
        group_perfect_pct = round((group_perfect / group_rated * 100), 1) if group_rated > 0 else 0.0

        # Title Tracks vs. B-Sides comparison
        title_avg = _round_half_up(t_rating_sum / t_cnt, 2) if t_cnt > 0 else None
        title_hit_rate = round((t_play / t_cnt * 100), 1) if t_cnt > 0 else 0.0

        bside_avg = _round_half_up(b_rating_sum / b_cnt, 2) if b_cnt > 0 else None
        bside_hit_rate = round((b_play / b_cnt * 100), 1) if b_cnt > 0 else 0.0

        title_bside_metrics = {
            "title_count": t_cnt,
            "title_avg": title_avg,
            "title_hit_rate": title_hit_rate,
            "title_perfect": t_perfect,
            "bside_count": b_cnt,
            "bside_avg": bside_avg,
            "bside_hit_rate": bside_hit_rate,
            "bside_perfect": b_perfect,
        }

        # Rating Distribution Histogram (Floored to whole numbers: 5★, 4★, 3★, 2★, 1★)
        histogram = []
        for step in [5, 4, 3, 2, 1]:
            c = dist_rows.get(step, 0)
            pct = round((c / total_rated * 100), 1) if total_rated > 0 else 0.0
            histogram.append({"rating": f"{step}★", "count": c, "pct": pct})

        # 3. Top-rated albums
        cur.execute("""
            SELECT 
                al.id AS album_id,
                al.title AS album_title,
                al.album_type,
                al.release_date,
                ROUND(AVG(tr.rating), 2) AS avg_score,
                COUNT(tr.id) AS track_count
            FROM albums al
            JOIN tracks tr ON tr.album_id = al.id
            WHERE al.artist_id = ? 
              AND tr.rating IS NOT NULL
              AND al.album_type IN ('EP', 'LP')
            GROUP BY al.id
            ORDER BY avg_score DESC, al.release_date DESC
            LIMIT 6
        """, (artist_id,))
        top_albums = [dict(r) for r in cur.fetchall()]

        # 4. Career Pacing & Metadata (artist_meta preloaded at start)
        valid_dates = [a["release_date"] for a in album_trend if a.get("release_date")] + [
            t["release_date"] for t in discovered_group_tracks
            if t.get("rating") is not None and t.get("release_date")
        ]
        earliest_release_str = sorted(valid_dates)[0] if valid_dates else None
        start_date = resolve_career_start_date(earliest_release_str, artist_meta.get("debut_year"))
        latest_release_str = sorted(valid_dates)[-1] if valid_dates else None
        active_years, tracks_per_year = calculate_artist_productivity(
            start_date=start_date,
            status=artist_meta.get("status"),
            latest_release_date_str=latest_release_str,
            total_catalog_tracks=total_catalog,
        )

        members = []
        parent_groups = []
        if artist_meta.get("id"):
            if artist_meta["artist_type"] == "group":
                members = get_artist_members(conn, artist_meta["id"])
            else:
                parent_groups = get_parent_groups_for_soloist(conn, artist_meta["id"])

        qwar_data = calculate_qwar_metrics(
            fives=fives,
            fours=fours,
            threes=threes,
            skips=skips,
            total_tracks=total_rated,
            avg_rating=overall_avg
        )

        return {
            "artist": artist,
            "albums": album_trend,
            "top_albums": top_albums,
            "overall_avg": overall_avg,
            "overall_hit_rate": overall_hit_rate,
            "group_avg": group_avg,
            "group_hit_rate": group_hit_rate,
            "group_tracks_count": group_rated,
            "total_rated_tracks": total_rated,
            "total_catalog_tracks": total_catalog,
            "raw_total_albums": raw_total_albums,
            "raw_total_tracks": raw_total_tracks,
            "plotted_albums_count": plotted_album_count,
            "group_perfect": group_perfect,
            "group_perfect_pct": group_perfect_pct,
            "overall_perfect": overall_perfect,
            "overall_perfect_pct": overall_perfect_pct,
            "active_years": active_years,
            "tracks_per_year": tracks_per_year,
            "generation": artist_meta.get("generation"),
            "status": artist_meta.get("status", "Active"),
            "artist_type": artist_meta.get("artist_type", "group"),
            "members": members,
            "parent_groups": parent_groups,
            "title_bsides": title_bside_metrics,
            "histogram": histogram,
            "compacted_qwar": qwar_data["compacted_qwar"],
            "legacy_qwar": qwar_data["legacy_qwar"],
            "qwar_per_track": qwar_data["qwar_per_track"],
        }


def calculate_artist_productivity(
    start_date: Optional[date],
    status: Optional[str],
    latest_release_date_str: Optional[str],
    total_catalog_tracks: int,
) -> Tuple[float, float]:
    """Computes career pacing metrics: active career span in years and annual track production.

    - If artist status is 'Disbanded' and a valid latest release date exists, career span
      freezes at the final release date.
    - For 'Active' or 'Hiatus' artists (or disbanded artists without release dates), active
      duration spans from career start up to date.today().
    - Protects against division by zero for rookie debut releases by applying a minimum
      active span of 0.1 years.

    Args:
        start_date: Career start date object from `resolve_career_start_date()`.
        status: Artist status string ('Active', 'Disbanded', 'Hiatus').
        latest_release_date_str: Release date string of the artist's most recent album.
        total_catalog_tracks: Substantive canonical catalog track count.

    Returns:
        Tuple[float, float]: (active_years, tracks_per_year).
    """
    end_date = date.today()
    if (status or "Active") == "Disbanded" and latest_release_date_str:
        try:
            parts = latest_release_date_str.split("-")
            end_date = date(int(parts[0]), int(parts[1]) if len(parts) > 1 else 1, int(parts[2]) if len(parts) > 2 else 1)
        except Exception:
            pass

    active_years = 0.0
    tracks_per_year = 0.0
    if start_date and end_date and end_date >= start_date:
        days_elapsed = (end_date - start_date).days
        active_years = max(0.1, round(days_elapsed / 365.25, 2))
        if total_catalog_tracks > 0:
            tracks_per_year = round(total_catalog_tracks / max(1.0, active_years), 1)

    return active_years, tracks_per_year


def calculate_qwar_metrics(
    fives: int, 
    fours: int, 
    threes: int, 
    skips: int, 
    total_tracks: int, 
    avg_rating: float
) -> Dict[str, float]:
    """Computes Quality-Weighted WAR (Q-WAR) metrics and listening density.

    Computes two complementary ranking perspectives:
    1. `compacted_qwar`:
       - Measures career legacy and discography depth.
       - Formula: `sqrt(Raw Points) * 10.0 * (Avg / 4.0)^1.5 * Gate`
       - Dampens extreme volume via square root so 90-track veterans and 20-track
         rising stars sit on an intuitive ~50-130 scale centered around 100.
       - Gate: `min(1.0, total_tracks / 12.0)` prevents rookie sample-size flukes (<12 tracks).
       
    2. `qwar_per_track`:
       - Measures listening density and expected enjoyment per track on shuffle.
       - Formula: `legacy_qwar / total_tracks`
       - Highlights pristine catalogs while showing how well veterans maintain quality
         over deep discographies.

    Raw point weights per tier:
    - 5★ (Flawless): +3.0 pts
    - 4★ (Great): +1.5 pts
    - 3★ (Solid Playable): +0.5 pts
    - Skips (<3★): -0.25 pts

    Args:
        fives: Count of tracks rated 5.0.
        fours: Count of tracks rated 4.0 - 4.9.
        threes: Count of tracks rated 3.0 - 3.9.
        skips: Count of rated tracks rated < 3.0.
        total_tracks: Total rated substantive tracks.
        avg_rating: Overall average track rating.

    Returns:
        Dict[str, float]: Dictionary containing:
            - compacted_qwar (float): Square-root volume dampened legacy score.
            - legacy_qwar (float): Uncompacted linear legacy score.
            - qwar_per_track (float): Listening density per track.
    """
    if total_tracks <= 0 or avg_rating is None:
        return {
            "compacted_qwar": 0.0,
            "legacy_qwar": 0.0,
            "qwar_per_track": 0.0
        }

    # Raw point allocation per track tier
    raw_points = (3.0 * fives) + (1.5 * fours) + (0.5 * threes) - (0.25 * skips)
    if raw_points <= 0:
        return {
            "compacted_qwar": 0.0,
            "legacy_qwar": 0.0,
            "qwar_per_track": 0.0
        }

    quality_multiplier = (avg_rating / 4.0) ** 1.5
    gate = min(1.0, total_tracks / 12.0)

    # 1. Uncompacted Legacy Points
    legacy_qwar = raw_points * quality_multiplier * gate

    # 2. Compacted Legacy Points (sqrt dampener centered ~100)
    compacted_qwar = (math.sqrt(raw_points) * 10.0) * quality_multiplier * gate

    # 3. Q-WAR Rate Per Track (Listening Density)
    qwar_per_track = legacy_qwar / total_tracks

    return {
        "compacted_qwar": round(compacted_qwar, 2),
        "legacy_qwar": round(legacy_qwar, 2),
        "qwar_per_track": round(qwar_per_track, 2)
    }
    

def get_all_artists_summary() -> List[Dict[str, Any]]:
    """Calculates summary career metrics and Q-WAR rankings for all artists.

    Powers the All-Artist Dashboards (Groups and Soloists) and Multi-Artist Compare view.
    Enforces `CANONICAL_UNIQUE_TRACK_SQL` across all aggregations to ensure single-artist
    deep dive and master dashboard numbers match with zero drift.

    Preloads soloist parent groups in a single bulk query to prevent N+1 performance bottlenecks.

    Returns:
        List[Dict[str, Any]]: List of artist summary dictionaries containing Q-WAR scores,
            track counts, hit rates, perfect 5 counts and percentages, career pacing, and affiliations.
    """
    with get_connection() as conn:
        cur = conn.cursor()

        # Precompute canonical included releases count per artist (matching get_artist_summary_metrics)
        cur.execute("""
            SELECT 
                al.artist_id,
                COUNT(DISTINCT al.id) as included_releases
            FROM albums al
            JOIN tracks tr ON tr.album_id = al.id
            WHERE tr.rating IS NOT NULL
              AND NOT EXISTS (
                    SELECT 1
                    FROM tracks t1
                    JOIN albums a1 ON t1.album_id = a1.id
                    JOIN tracks t2 ON LOWER(TRIM(t1.title)) = LOWER(TRIM(t2.title))
                    JOIN albums a2 ON t2.album_id = a2.id
                    WHERE a1.id = al.id
                      AND a2.id != al.id
                      AND a2.artist_id = al.artist_id
                      AND (
                          (
                              al.album_type = 'Single'
                              AND a2.album_type IN ('EP', 'LP')
                              AND (
                                  release_day_gap(a2.release_date, al.release_date) BETWEEN 0 AND 120
                                  OR release_day_gap(al.release_date, a2.release_date) >= 0
                              )
                          )
                          OR
                          (
                              al.album_type = 'Single'
                              AND a2.album_type = 'Single'
                              AND (
                                  release_day_gap(a2.release_date, al.release_date) BETWEEN 0 AND 120
                                  OR release_day_gap(al.release_date, a2.release_date) >= 0
                              )
                              AND (SELECT COUNT(*) FROM tracks WHERE album_id = a1.id AND rating IS NOT NULL) < 
                                  (SELECT COUNT(*) FROM tracks WHERE album_id = a2.id AND rating IS NOT NULL)
                          )
                          OR
                          (
                              al.album_type IN ('EP', 'LP')
                              AND a2.album_type IN ('EP', 'LP')
                              AND is_alt_lang(al.title) = 1
                              AND is_alt_lang(a2.title) = 0
                          )
                      )
              )
            GROUP BY al.artist_id
        """)
        included_releases_map = dict(cur.fetchall())

        cur.execute(f"""
            SELECT 
                ar.id AS artist_id,
                ar.name AS artist,
                ar.generation,
                ar.status,
                ar.debut_year,
                ar.artist_type,
                COUNT(DISTINCT al.id) AS raw_total_albums,
                COUNT(tr.id) AS total_catalog_tracks,
                COUNT(tr.rating) AS total_rated_tracks,
                SUM(tr.rating) AS overall_rating_sum,
                ROUND(AVG(tr.rating), 2) AS overall_avg,
                ROUND(AVG(CASE WHEN COALESCE(tr.track_type, 'core') = 'core' THEN tr.rating END), 2) AS group_avg,
                COUNT(CASE WHEN tr.rating >= 3.0 THEN 1 END) AS playable_tracks,
                COUNT(CASE WHEN tr.rating >= 5.0 THEN 1 END) AS overall_perfect,
                COUNT(CASE WHEN tr.rating >= 5.0 AND COALESCE(tr.track_type, 'core') = 'core' THEN 1 END) AS group_perfect,
                
                -- Tier breakdowns for Q-WAR calculation
                COUNT(CASE WHEN tr.rating >= 5.0 THEN 1 END) AS fives,
                COUNT(CASE WHEN tr.rating >= 4.0 AND tr.rating < 5.0 THEN 1 END) AS fours,
                COUNT(CASE WHEN tr.rating >= 3.0 AND tr.rating < 4.0 THEN 1 END) AS threes,
                COUNT(CASE WHEN tr.rating IS NOT NULL AND tr.rating < 3.0 THEN 1 END) AS skips,

                -- Title Track vs. B-Side splits (matching get_artist_summary_metrics)
                COUNT(CASE WHEN tr.rating IS NOT NULL AND tr.is_title_track = 1 THEN 1 END) AS title_count,
                SUM(CASE WHEN tr.rating IS NOT NULL AND tr.is_title_track = 1 THEN tr.rating END) AS title_rating_sum,
                COUNT(CASE WHEN tr.rating IS NOT NULL AND COALESCE(tr.is_title_track, 0) = 0 AND COALESCE(tr.track_type, 'core') IN ('core', 'solo', 'unit') THEN 1 END) AS bside_count,
                SUM(CASE WHEN tr.rating IS NOT NULL AND COALESCE(tr.is_title_track, 0) = 0 AND COALESCE(tr.track_type, 'core') IN ('core', 'solo', 'unit') THEN tr.rating END) AS bside_rating_sum,
                
                (SELECT MIN(al2.release_date) FROM albums al2 WHERE al2.artist_id = ar.id AND EXISTS (SELECT 1 FROM tracks tr2 WHERE tr2.album_id = al2.id AND tr2.rating IS NOT NULL)) AS earliest_release,
                (SELECT MAX(al2.release_date) FROM albums al2 WHERE al2.artist_id = ar.id AND EXISTS (SELECT 1 FROM tracks tr2 WHERE tr2.album_id = al2.id AND tr2.rating IS NOT NULL)) AS latest_release
            FROM artists ar
            LEFT JOIN albums al ON al.artist_id = ar.id
            LEFT JOIN tracks tr ON tr.album_id = al.id 
                AND {CANONICAL_UNIQUE_TRACK_SQL}
            GROUP BY ar.id
            ORDER BY ar.name COLLATE NOCASE ASC
        """)

        rows = cur.fetchall()
        
        # Preload rated album summaries per artist in 1 bulk query for Category Leaders
        cur.execute("""
            SELECT
                al.artist_id,
                al.id AS album_id,
                al.title AS album_title,
                al.album_type,
                al.release_date,
                ROUND(AVG(tr.rating), 2) AS avg_score,
                COUNT(tr.rating) AS rated_tracks
            FROM albums al
            JOIN tracks tr ON tr.album_id = al.id
            WHERE tr.rating IS NOT NULL
            GROUP BY al.id
            ORDER BY avg_score DESC, rated_tracks DESC
        """)
        albums_by_artist: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
        for alb_row in cur.fetchall():
            albums_by_artist[alb_row["artist_id"]].append(dict(alb_row))

        # Preload parent groups and discovered parent-group solo/unit tracks for all soloists in bulk
        solo_parent_map = _fetch_solo_parent_map(cur)
        solo_ids = [r["artist_id"] for r in rows if (r["artist_type"] or "group") == "solo"]
        solo_group_tracks_map: Dict[int, List[Dict[str, Any]]] = {}
        if solo_ids:
            solo_placeholders = ",".join("?" for _ in solo_ids)
            cur.execute(f"""
                SELECT al.artist_id, tr.title
                FROM tracks tr
                JOIN albums al ON tr.album_id = al.id
                WHERE al.artist_id IN ({solo_placeholders})
                  AND {CANONICAL_UNIQUE_TRACK_SQL}
            """, solo_ids)
            own_titles_by_soloist: Dict[int, set] = defaultdict(set)
            for tr_row in cur.fetchall():
                if tr_row["title"]:
                    own_titles_by_soloist[tr_row["artist_id"]].add(tr_row["title"].strip().casefold())
            solo_group_tracks_map = _fetch_soloist_group_tracks(conn, solo_ids, own_titles_by_soloist)
        
        results = []

        for r in rows:
            artist_type = r["artist_type"] or "group"
            total_catalog_tracks = r["total_catalog_tracks"] or 0
            total_rated = r["total_rated_tracks"] or 0
            overall_rating_sum = r["overall_rating_sum"] or 0.0
            playable = r["playable_tracks"] or 0
            overall_perfect = r["overall_perfect"] or 0
            fives = r["fives"] or 0
            fours = r["fours"] or 0
            threes = r["threes"] or 0
            skips = r["skips"] or 0
            title_count = r["title_count"] or 0
            title_rating_sum = r["title_rating_sum"] or 0.0
            bside_count = r["bside_count"] or 0
            bside_rating_sum = r["bside_rating_sum"] or 0.0
            earliest_release = r["earliest_release"]
            latest_release = r["latest_release"]

            if artist_type == "solo":
                for dt in solo_group_tracks_map.get(r["artist_id"], ()):
                    total_catalog_tracks += 1
                    dr = dt.get("rating")
                    if dr is not None:
                        total_rated += 1
                        overall_rating_sum += dr
                        if dr >= 3.0:
                            playable += 1
                        if dr >= 5.0:
                            overall_perfect += 1
                            fives += 1
                        elif dr >= 4.0:
                            fours += 1
                        elif dr >= 3.0:
                            threes += 1
                        else:
                            skips += 1
                        if dt.get("is_title_track") == 1:
                            title_count += 1
                            title_rating_sum += dr
                        elif dt.get("track_type") in ("core", "solo", "unit"):
                            bside_count += 1
                            bside_rating_sum += dr
                        d_date = dt.get("release_date")
                        if d_date:
                            if not earliest_release or d_date < earliest_release:
                                earliest_release = d_date
                            if not latest_release or d_date > latest_release:
                                latest_release = d_date

            overall_avg = _round_half_up(overall_rating_sum / total_rated, 2) if total_rated > 0 else 0.0
            overall_hit_rate = round((playable / total_rated * 100), 1) if total_rated > 0 else 0.0
            overall_perfect_pct = round((overall_perfect / total_rated * 100), 1) if total_rated > 0 else 0.0
            title_avg = _round_half_up(title_rating_sum / title_count, 2) if title_count > 0 else None
            bside_avg = _round_half_up(bside_rating_sum / bside_count, 2) if bside_count > 0 else None
            
            # Calculate career duration & tracks/year
            start_date = resolve_career_start_date(earliest_release, r["debut_year"])

            active_years, tracks_per_year = calculate_artist_productivity(
                start_date=start_date,
                status=r["status"],
                latest_release_date_str=latest_release,
                total_catalog_tracks=total_catalog_tracks,
            )

            # Calculate Q-WAR metrics
            qwar_data = calculate_qwar_metrics(
                fives=fives,
                fours=fours,
                threes=threes,
                skips=skips,
                total_tracks=total_rated,
                avg_rating=overall_avg
            )

            # Parent groups for soloists
            parent_groups = []
            if artist_type == "solo":
                parent_groups = solo_parent_map.get(r["artist_id"], [])

            results.append({
                "artist_id": r["artist_id"],
                "artist": r["artist"],
                "generation": r["generation"],
                "status": r["status"] or "Active",
                "debut_year": r["debut_year"],
                "artist_type": artist_type,
                "parent_groups": parent_groups,
                "raw_total_albums": r["raw_total_albums"],
                "plotted_albums_count": included_releases_map.get(r["artist_id"], 0),
                "total_catalog_tracks": total_catalog_tracks,
                "total_rated_tracks": total_rated,
                "overall_avg": overall_avg,
                "group_avg": r["group_avg"],
                "overall_hit_rate": overall_hit_rate,
                "overall_perfect": overall_perfect,
                "overall_perfect_pct": overall_perfect_pct,
                "group_perfect": r["group_perfect"] or 0,
                "title_count": title_count,
                "title_avg": title_avg,
                "bside_count": bside_count,
                "bside_avg": bside_avg,
                "albums": albums_by_artist.get(r["artist_id"], []),
                "active_years": active_years,
                "tracks_per_year": tracks_per_year,
                
                # Q-WAR Metrics
                "compacted_qwar": qwar_data["compacted_qwar"],
                "legacy_qwar": qwar_data["legacy_qwar"],
                "qwar_per_track": qwar_data["qwar_per_track"]
            })

        return results
        

def sync_soloist_affiliations(
    conn: sqlite3.Connection, 
    soloist_id: int, 
    soloist_name: str, 
    parent_groups_str: str
):
    """Syncs a soloist's affiliations into `artist_members` from a comma-separated string.

    Supports tokens where a leading asterisk '*' denotes a former group affiliation
    (e.g., '*Wonder Girls, 4MINUTE, *Trouble Maker').
    If a parent group exists in the database:
    - If the soloist already exists on the group's roster, updates the link and former status.
    - If the soloist is not yet on the roster, inserts a new roster record linking the solo profile.

    Args:
        conn: Active SQLite connection.
        soloist_id: Primary key ID of the solo artist.
        soloist_name: Solo artist name string.
        parent_groups_str: Comma-separated list of parent group names (with optional '*' prefixes).
    """
    cur = conn.cursor()
    clean_pg_str = parent_groups_str.strip() if parent_groups_str and parent_groups_str.strip() else None
    cur.execute("UPDATE artists SET parent_groups = ? WHERE id = ?", (clean_pg_str, soloist_id))
    active_group_ids = []
    
    for group_name, is_former in _parse_parent_groups_str(parent_groups_str):
        # Find group by name (Unicode case-insensitive)
        cur.execute("SELECT id FROM artists WHERE py_lower(name) = py_lower(?) AND artist_type = 'group'", (group_name,))
        grp = cur.fetchone()
        if grp:
            group_id = grp["id"]
            active_group_ids.append(group_id)
            # Check if this soloist is already listed in the group's roster
            cur.execute("""
                SELECT id FROM artist_members 
                WHERE group_id = ? AND (solo_artist_id = ? OR py_lower(member_name) = py_lower(?))
            """, (group_id, soloist_id, soloist_name))
            existing_member = cur.fetchone()
            
            if existing_member:
                cur.execute("""
                    UPDATE artist_members 
                    SET solo_artist_id = ?, is_former = ?, member_name = ? 
                    WHERE id = ?
                """, (soloist_id, is_former, soloist_name, existing_member["id"]))
            else:
                # Add them to the group's roster with the solo link established
                cur.execute("""
                    INSERT INTO artist_members (group_id, member_name, solo_artist_id, is_former, display_order)
                    VALUES (?, ?, ?, ?, 99)
                """, (group_id, soloist_name, soloist_id, is_former))

    # Remove stale solo links for groups no longer affiliated
    if active_group_ids:
        placeholders = ",".join("?" for _ in active_group_ids)
        cur.execute(
            f"UPDATE artist_members SET solo_artist_id = NULL WHERE solo_artist_id = ? AND group_id NOT IN ({placeholders})",
            [soloist_id] + active_group_ids,
        )
    else:
        cur.execute("UPDATE artist_members SET solo_artist_id = NULL WHERE solo_artist_id = ?", (soloist_id,))
