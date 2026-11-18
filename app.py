"""FastAPI Web Application & REST API for K-Pop DAT (Data & Analytics Tracker).

Provides routes for single & bulk MusicBrainz imports, album/track catalog management,
group roster & soloist linking, full CSV export, and statistical analytics (Q-WAR, EMA trajectories).
"""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional, Any
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import csv
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime

import database
from fetcher import (
    fetch_album_metadata, 
    get_artist_discography, 
    get_tracklist_for_release_group
)
from database import (
    init_db,
    checkpoint_wal,
    add_watchlist_artist,
    commit_album_payload,
    create_artist_profile,
    delete_album,
    delete_artist_profile,
    delete_watchlist_artist,
    get_album_details,
    get_all_artist_profiles,
    get_all_artists_summary,
    get_all_library_albums,
    get_artist_summary_metrics,
    get_artists_with_catalog_ratings,
    get_csv_export_data,
    get_existing_artist_albums,
    get_existing_track_defaults,
    get_filtered_tracks,
    get_full_catalog_json_data,
    get_paginated_library_artists,
    get_watchlist_artists,
    restore_catalog_from_db,
    restore_catalog_from_json,
    update_artist_profile,
    update_single_track_rating,
    update_watchlist_artist,
    update_watchlist_prediction,
)
from backup import create_snapshot

STC_SERVER_PORT = int(os.environ.get("STC_SERVER_PORT") or os.environ.get("MTC_SERVER_PORT", "5001"))


def _resolve_stc_dir() -> Path:
    """Finds the companion SonicTaste (STC) directory across supported sibling folder names."""
    for name in ("STC App", "SonicTaste", "kpop-ml-sonictaste", "MTC App"):
        cand = database.BASE_DIR.parent / name
        try:
            if cand.exists():
                return cand
        except OSError:
            continue
    return database.BASE_DIR.parent / "MTC App"


_STC_DIR = _resolve_stc_dir()


def _resolve_stc_python() -> Path:
    """Finds the Python executable for the SonicTaste sidecar across portable venv layouts."""
    env_override = os.environ.get("STC_PYTHON_EXE") or os.environ.get("MTC_PYTHON_EXE")
    if env_override:
        return Path(env_override)
    candidates = [
        _STC_DIR / "venv" / "Scripts" / "python.exe",
        _STC_DIR / ".venv" / "Scripts" / "python.exe",
        _STC_DIR / "venv" / "bin" / "python",
        _STC_DIR / ".venv" / "bin" / "python",
        Path.home() / "venvs" / "stc" / "Scripts" / "python.exe",
        Path.home() / "venvs" / "stc" / "bin" / "python",
        Path.home() / "venvs" / "mtc" / "Scripts" / "python.exe",
        Path.home() / "venvs" / "mtc" / "bin" / "python",
    ]
    for cand in candidates:
        try:
            if cand.exists():
                return cand
        except OSError:
            continue
    return Path(sys.executable)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Initializes SQLite schema and auto-manages the SonicTaste (STC) sidecar server lifecycle."""
    init_db()
    sidecar_proc = None
    server_script = _STC_DIR / "server.py"
    stc_python = _resolve_stc_python()
    if stc_python.exists() and server_script.exists():
        env = os.environ.copy()
        env["STC_SERVER_PORT"] = str(STC_SERVER_PORT)
        env["MTC_SERVER_PORT"] = str(STC_SERVER_PORT)
        try:
            sidecar_proc = subprocess.Popen(
                [str(stc_python), str(server_script)],
                cwd=str(_STC_DIR),
                env=env,
            )
        except Exception as exc:
            print(f"[WARN] Could not start STC sidecar: {exc}")
    try:
        yield
    finally:
        if sidecar_proc is not None and sidecar_proc.poll() is None:
            sidecar_proc.terminate()
            try:
                sidecar_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                sidecar_proc.kill()


app = FastAPI(
    title="K-Pop Rating Tracker",
    description="Discography catalog, score tracker, and comprehensive career analytics engine.",
    lifespan=_lifespan,
)
app.mount("/static", StaticFiles(directory=str(database.RESOURCE_DIR / "static")), name="static")


class TrackIn(BaseModel):
    """Pydantic model representing an individual track input for album commit."""
    id: Optional[int] = None
    track_number: int
    title: str
    rating: Optional[float] = Field(default=None, ge=1.0, le=5.0)
    is_title_track: bool = False
    track_type: Optional[str] = "core"
    notes: Optional[str] = None

class AlbumPayload(BaseModel):
    """Pydantic model representing an album staging and commit payload."""
    album_id: Optional[int] = None
    artist: str
    title: str
    release_date: Optional[str] = None
    album_type: str = "EP"
    mb_release_group_id: Optional[str] = None
    tracks: List[TrackIn]

class ArtistUpdate(BaseModel):
    """Pydantic model for updating artist metadata, roster, or parent group affiliations."""
    name: str
    debut_year: Optional[int] = None
    generation: Optional[str] = None
    status: Optional[str] = "Active"
    artist_type: Optional[str] = "group"
    members: Optional[List[str]] = None
    parent_groups: Optional[str] = None

class TrackScoreUpdate(BaseModel):
    """Pydantic model for inline track rating updates from the table view."""
    rating: Optional[float] = Field(default=None, ge=1.0, le=5.0)

class WatchlistIn(BaseModel):
    """Pydantic model representing an artist watchlist prospect entry."""
    name: str
    artist_type: Optional[str] = "group"
    generation: Optional[str] = None
    status: Optional[str] = "Active"
    parent_groups: Optional[str] = None
    members: Optional[str] = None
    notes: Optional[str] = None

class SnapshotIn(BaseModel):
    """Pydantic model representing an optional label for application snapshots."""
    label: Optional[str] = None


# ---------------------------------------------------------------------------
# CORE VIEWS & EXPORTS
# ---------------------------------------------------------------------------

@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    """Returns an empty 204 No Content response for browser favicon requests."""
    return Response(status_code=204)

@app.get("/", response_class=HTMLResponse)
def index():
    """Renders the single-page application interface from templates/index.html."""
    with open(database.RESOURCE_DIR / "templates" / "index.html", "r", encoding="utf-8") as f:
        return f.read()

def _sanitize_csv_cell(val: Any) -> Any:
    """Escapes leading spreadsheet formula injection characters (=, +, -, @).

    Args:
        val: Any cell value.

    Returns:
        Any: Sanitized value prefixed with a single quote if string starts with formula triggers.
    """
    if isinstance(val, str) and val and val[0] in ("=", "+", "-", "@"):
        return f"'{val}"
    return val

@app.get("/api/export/csv")
def export_csv():
    """Exports the entire database catalog to a downloadable CSV file.

    Includes artists, group rosters, parent groups, albums, and tracks with formula escaping.

    Returns:
        Response: Attached CSV content formatted as `kpop_library_export.csv`.

    Raises:
        HTTPException: If an unexpected error occurs during data extraction.
    """
    try:
        checkpoint_wal()
        roster_map, rows = get_csv_export_data()
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Write Header
        writer.writerow([
            "Artist", "Artist Type", "Affiliated Groups", "Generation", "Debut Year", "Artist Status", "Members",
            "Album Title", "Album Type", "Release Date", "MB Release Group ID",
            "Track Number", "Track Title", "Track Type", "Title Track", "Rating", "Notes"
        ])
        
        for r in rows:
            members_str = ", ".join(roster_map.get(r["artist_id"], []))
            writer.writerow([
                _sanitize_csv_cell(r["artist"]),
                r["artist_type"] or "group",
                _sanitize_csv_cell(r["parent_group_name"] or ""),
                r["generation"] or "",
                r["debut_year"] or "",
                r["artist_status"] or "Active",
                _sanitize_csv_cell(members_str),
                _sanitize_csv_cell(r["album_title"]),
                r["album_type"] or "",
                r["release_date"] or "",
                r["mb_release_group_id"] or "",
                r["track_number"],
                _sanitize_csv_cell(r["track_title"]),
                r["track_type"] or "core",
                "Yes" if r["is_title_track"] else "No",
                r["rating"] if r["rating"] is not None else "",
                _sanitize_csv_cell(r["notes"] or "")
            ])
        
        output.seek(0)
        return Response(
            content=output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=kpop_library_export.csv"}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/export/db")
def export_db():
    """Flushes SQLite WAL journal and returns the raw database file as an attachment.

    Returns:
        FileResponse: Downloadable binary .db file.
    """
    try:
        checkpoint_wal()
        db_path = database.DB_NAME
        if not os.path.exists(db_path):
            raise HTTPException(status_code=404, detail="Database file not found.")
        filename = f"kpop_ratings_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        return FileResponse(
            db_path,
            media_type="application/x-sqlite3",
            filename=filename
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/export/json")
def export_json():
    """Flushes SQLite WAL and returns the complete structured database catalog as a JSON download.

    Returns:
        Response: Downloadable JSON response with Content-Disposition header.
    """
    try:
        checkpoint_wal()
        data = get_full_catalog_json_data()
        content = json.dumps(data, indent=2, ensure_ascii=False)
        filename = f"kpop_catalog_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        return Response(
            content=content,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/backup/snapshot")
def api_create_snapshot(payload: Optional[SnapshotIn] = None):
    """Creates an atomic application and database checkpoint in backups/snapshots/.

    Returns:
        dict: Status dictionary with created folder name and full path.
    """
    try:
        checkpoint_wal()
        label = (payload.label or "").strip() if payload else ""
        snapshot_dir = create_snapshot(label)
        return {
            "status": "success",
            "snapshot_folder": snapshot_dir.name,
            "path": str(snapshot_dir)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create snapshot: {str(e)}")


@app.post("/api/restore")
async def api_restore_catalog(
    request: Request,
    filename: str = Query(..., description="Original filename of the uploaded .json or .db backup")
):
    """Restores the full catalog from an uploaded .json or .db backup file.

    Always creates a `pre_restore` snapshot before mutating the live database,
    and preserves original row IDs (including `tracks.id`) for SonicTaste alignment.
    """
    clean_name = Path(filename).name.lower()
    if clean_name.endswith(".json"):
        fmt = "json"
    elif clean_name.endswith(".db"):
        fmt = "db"
    else:
        raise HTTPException(status_code=400, detail="Unsupported file format. Please upload a .json or .db file.")

    raw_bytes = await request.body()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="Uploaded backup file is empty.")

    try:
        if fmt == "json":
            try:
                payload = json.loads(raw_bytes.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                raise HTTPException(status_code=400, detail=f"Invalid JSON file: {e}")

            checkpoint_wal()
            snapshot_dir = create_snapshot("pre_restore")
            counts = restore_catalog_from_json(payload)
        else:
            tmp_path = None
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as tmp_file:
                    tmp_file.write(raw_bytes)
                    tmp_path = tmp_file.name

                checkpoint_wal()
                snapshot_dir = create_snapshot("pre_restore")
                counts = restore_catalog_from_db(tmp_path)
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass

        return {
            "status": "success",
            "format": fmt,
            "snapshot_folder": snapshot_dir.name,
            "counts": counts,
        }
    except HTTPException:
        raise
    except (ValueError, sqlite3.DatabaseError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Restore failed: {str(e)}")


# ---------------------------------------------------------------------------
# MUSICBRAINZ & BULK IMPORT
# ---------------------------------------------------------------------------
def enrich_staged_album(data: dict) -> dict:
    """Pre-fills title track flags and prior ratings based on catalog releases.

    If any track on the incoming album was previously cataloged under the same
    artist and is not classified as alt_lang or non_canon, its `is_title_track`
    flag and most recent `rating` (when currently unset) are propagated.

    Args:
        data: Staged album payload dictionary.

    Returns:
        dict: The enriched album payload dictionary.
    """
    artist = data.get("artist")
    if not artist:
        return data
    prior_titles, prior_ratings = get_existing_track_defaults(artist)
    if not prior_titles and not prior_ratings:
        return data
    for track in data.get("tracks", []):
        if track.get("track_type") in ("alt_lang", "non_canon"):
            continue
        norm_title = (track.get("title") or "").strip().casefold()
        if norm_title in prior_titles:
            track["is_title_track"] = True
        if track.get("rating") is None and norm_title in prior_ratings:
            track["rating"] = prior_ratings[norm_title]
    return data


@app.get("/api/fetch", response_model=AlbumPayload)
def fetch_release(artist: str, album: str):
    """Queries MusicBrainz for an album and parses its tracklist for staging.

    Args:
        artist: Artist name string.
        album: Album title string.

    Returns:
        AlbumPayload: Structured album payload with parsed tracks.

    Raises:
        HTTPException(404): If no matching release is found on MusicBrainz.
        HTTPException(500): On MusicBrainz API communication failure.
    """
    try:
        data = fetch_album_metadata(artist.strip(), album.strip())
        if not data:
            raise HTTPException(status_code=404, detail=f"No release found for '{album}' by '{artist}'.")
        return enrich_staged_album(data)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/artist-discography")
def api_artist_discography(artist: str):
    """Fetches official release groups for bulk queue import, flagging existing catalog entries.

    Args:
        artist: Artist name string.

    Returns:
        dict: Contains 'artist' (str) and 'releases' (List[dict]) with 'in_library' flags.

    Raises:
        HTTPException(404): If artist discography cannot be resolved.
        HTTPException(500): On communication failure.
    """
    try:
        releases = get_artist_discography(artist.strip())
        if not releases:
            raise HTTPException(status_code=404, detail=f"No discography found for '{artist}'.")

        existing_albums = get_existing_artist_albums(artist)
        existing_mbids = {r["mb_release_group_id"] for r in existing_albums if r["mb_release_group_id"]}
        
        # Guard against None values in existing SQLite records
        existing_signatures = {
            ((r["title"] or "").strip().lower(), (r["album_type"] or "EP").strip().lower())
            for r in existing_albums
        }

        # Guard against None values in MusicBrainz response
        for r in releases:
            rg_id = r.get("rg_id")
            title = (r.get("title") or "").strip().lower()
            ptype = (r.get("primary_type") or "EP").strip().lower()
            title_sig = (title, ptype)
            r["in_library"] = (rg_id and rg_id in existing_mbids) or (title_sig in existing_signatures)

        return {"artist": artist, "releases": releases}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/fetch-rg", response_model=AlbumPayload)
def api_fetch_rg(rg_id: str, artist: str):
    """Resolves the definitive tracklist for a specific MusicBrainz release group.

    Args:
        rg_id: MusicBrainz release-group UUID string.
        artist: Fallback artist name string.

    Returns:
        AlbumPayload: Structured album payload with parsed tracklist.

    Raises:
        HTTPException(404): If the release group or tracklist cannot be resolved.
        HTTPException(500): On communication error.
    """
    try:
        data = get_tracklist_for_release_group(rg_id, fallback_artist=artist.strip())
        if not data:
            raise HTTPException(status_code=404, detail="Tracklist could not be resolved.")
        return enrich_staged_album(data)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# ALBUM & TRACK CRUD
# ---------------------------------------------------------------------------

@app.get("/api/albums/{album_id}", response_model=AlbumPayload)
def get_album(album_id: int):
    """Retrieves full album details and ordered tracklist for editing.

    Args:
        album_id: Primary key ID of the album.

    Returns:
        AlbumPayload: Full album and track payload.

    Raises:
        HTTPException(404): If album does not exist.
    """
    data = get_album_details(album_id)
    if not data:
        raise HTTPException(status_code=404, detail="Album not found.")
    data["album_id"] = data["id"]
    return data

@app.post("/api/save")
def save_release(payload: AlbumPayload):
    """Commits an album and its tracklist to the database (create or update).

    Graduates any matching prospective artist from the watchlist upon save.

    Args:
        payload: AlbumPayload with album metadata and tracklist.

    Returns:
        dict: Status response with `status`, `album_id`, and `updated` flag.

    Raises:
        HTTPException(500): On database constraint or write failure.
    """
    try:
        is_update = bool(payload.album_id)
        saved_id = commit_album_payload(payload.model_dump(), album_id=payload.album_id)
        return {"status": "success", "album_id": saved_id, "updated": is_update}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.delete("/api/albums/{album_id}")
def remove_album(album_id: int):
    """Deletes an album and cascades track deletion via foreign key.

    Args:
        album_id: Primary key ID of the album to delete.

    Returns:
        dict: `{"status": "deleted"}`.

    Raises:
        HTTPException(500): On database error.
    """
    try:
        delete_album(album_id)
        return {"status": "deleted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/library")
def get_library():
    """Returns all library albums with track counts, average scores, and release dates."""
    return get_all_library_albums()

@app.get("/api/tracks")
def get_all_tracks(
    artist: Optional[str] = None,
    min_rating: Optional[float] = None,
    title_only: Optional[bool] = False,
    search: Optional[str] = None
):
    """Returns tracks filtered by artist, minimum rating, title-track status, or text search."""
    return get_filtered_tracks(
        artist=artist,
        min_rating=min_rating,
        title_only=title_only,
        search=search
    )

@app.patch("/api/tracks/{track_id}/rating")
def update_track_rating(track_id: int, payload: TrackScoreUpdate):
    """Updates only the rating of an individual track directly from the table view.

    Args:
        track_id: Primary key ID of the track.
        payload: TrackScoreUpdate containing the new float rating (1.0 - 5.0) or None.

    Returns:
        dict: Status response with updated `track_id` and `rating`.

    Raises:
        HTTPException(404): If track is not found.
    """
    updated = update_single_track_rating(track_id, payload.rating)
    if not updated:
        raise HTTPException(status_code=404, detail="Track not found.")
    return {"status": "updated", "track_id": track_id, "rating": payload.rating}


# ---------------------------------------------------------------------------
# ARTIST DIRECTORY & PROFILES
# ---------------------------------------------------------------------------

@app.get("/api/artists")
def get_all_artists():
    """Returns all artist profiles preloaded with group members and soloist affiliations."""
    return get_all_artist_profiles()

@app.post("/api/artists")
def create_artist(payload: ArtistUpdate):
    """Creates a new artist profile with metadata, group member rosters, or soloist affiliations."""
    try:
        artist_id = create_artist_profile(payload.model_dump())
        return {"status": "created", "id": artist_id}
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail=f"Artist '{payload.name}' already exists.")

@app.put("/api/artists/{artist_id}")
def update_artist(artist_id: int, payload: ArtistUpdate):
    """Updates artist metadata, group member rosters, or soloist affiliations."""
    update_artist_profile(artist_id, payload.model_dump())
    return {"status": "updated"}

@app.delete("/api/artists/{artist_id}")
def remove_artist(artist_id: int):
    """Deletes an artist profile, cascading albums, tracks, and member rosters."""
    delete_artist_profile(artist_id)
    return {"status": "deleted"}

@app.get("/api/library/artists")
def api_get_library_artists(
    search: Optional[str] = None,
    generation: Optional[str] = None,
    status: Optional[str] = None,
    artist_type: Optional[str] = None,
    sort_by: str = "name_asc",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100)
):
    """Returns paginated artist directory with aggregated album/track counts and average scores.

    Supports server-side filtering, sorting, and pagination.
    """
    try:
        return get_paginated_library_artists(
            search=search,
            generation=generation,
            status=status,
            artist_type=artist_type,
            sort_by=sort_by,
            page=page,
            page_size=page_size
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/analytics/artists")
def get_artists_with_ratings():
    """Returns sorted names of all artists who have at least one album cataloged."""
    return get_artists_with_catalog_ratings()


# ---------------------------------------------------------------------------
# ANALYTICS & STATISTICAL METRICS
# ---------------------------------------------------------------------------

@app.get("/api/analytics/artist-summary")
def get_artist_summary(artist: str):
    """Computes career metrics, standouts, and chronological album trajectory for an artist.

    Enforces the Canonical Track Invariant (`CANONICAL_UNIQUE_TRACK_SQL`) to prevent
    duplicate track counts and distorted averages.
    """
    return get_artist_summary_metrics(artist.strip())

@app.get("/api/analytics/compare")
def compare_artists(artists: str):
    """Accepts comma-separated artist names and returns comparative career summaries (up to 4)."""
    artist_list = [a.strip() for a in artists.split(",") if a.strip()]
    if not artist_list:
        raise HTTPException(status_code=400, detail="No artists provided.")

    return [get_artist_summary_metrics(artist_name) for artist_name in artist_list[:4]]

@app.get("/api/analytics/all-artists")
def api_get_all_artists_analytics():
    """Returns high-level career metrics, Q-WAR rankings, and pacing for all artists."""
    try:
        return get_all_artists_summary()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# ARTIST WATCHLIST (PROSPECTS QUEUE)
# ---------------------------------------------------------------------------

@app.get("/api/watchlist")
def api_get_watchlist(artist_type: Optional[str] = None):
    """Returns queued watchlist artists sorted by predicted compatibility then date added."""
    return get_watchlist_artists(artist_type)

@app.post("/api/watchlist")
def api_add_watchlist(payload: WatchlistIn):
    """Adds a new prospective artist to the discovery watchlist queue."""
    try:
        new_id = add_watchlist_artist(payload.model_dump())
        return {"status": "success", "id": new_id}
    except Exception as e:
        if "UNIQUE constraint failed" in str(e):
            raise HTTPException(status_code=400, detail="Artist is already in the watchlist.")
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/watchlist/{watchlist_id}")
def api_update_watchlist(watchlist_id: int, payload: WatchlistIn):
    """Updates metadata or notes for an existing watchlist entry."""
    try:
        success = update_watchlist_artist(watchlist_id, payload.model_dump())
        if not success:
            raise HTTPException(status_code=404, detail="Watchlist entry not found.")
        return {"status": "success"}
    except HTTPException:
        raise
    except Exception as e:
        if "UNIQUE constraint failed" in str(e):
            raise HTTPException(status_code=400, detail="An artist with this name is already in the watchlist.")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/watchlist/{watchlist_id}")
def api_delete_watchlist(watchlist_id: int):
    """Removes a prospective artist from the discovery watchlist queue."""
    try:
        delete_watchlist_artist(watchlist_id)
        return {"status": "deleted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _call_stc_sidecar(
    path: str, payload: Optional[dict] = None, timeout: int = 45, method: str = "POST"
) -> dict:
    """Sends a request to the local SonicTaste (STC) sidecar server and parses its JSON response."""
    req_body = json.dumps(payload or {}).encode("utf-8") if method != "GET" else None
    headers = {"Content-Type": "application/json"} if method != "GET" else {}
    req = urllib.request.Request(
        f"http://127.0.0.1:{STC_SERVER_PORT}{path}",
        data=req_body,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(err_body).get("detail", err_body)
        except Exception:
            detail = err_body
        raise HTTPException(status_code=e.code, detail=detail)
    except TimeoutError:
        raise HTTPException(
            status_code=504,
            detail="SonicTaste (STC) prediction server timed out while processing the request.",
        )
    except urllib.error.URLError:
        raise HTTPException(
            status_code=503,
            detail="SonicTaste (STC) prediction server is starting or unavailable. Please try again in a moment.",
        )


@app.post("/api/watchlist/{watchlist_id}/predict")
def api_predict_watchlist_artist(watchlist_id: int, top_n: int = Query(default=10, ge=1, le=15)):
    """Queries the SonicTaste (STC) sidecar server for an artist's top-track taste prediction and persists it."""
    entries = get_watchlist_artists()
    entry = next((e for e in entries if e["id"] == watchlist_id), None)
    if not entry:
        raise HTTPException(status_code=404, detail="Watchlist entry not found.")

    result = _call_stc_sidecar("/predict/artist", {"artist": entry["name"], "top_n": top_n}, timeout=45)

    tracks_json = json.dumps(result.get("tracks", []), ensure_ascii=False)
    update_watchlist_prediction(
        watchlist_id=watchlist_id,
        predicted_score=float(result["avg_score"]),
        predicted_hits=int(result["hit_count"]),
        predicted_total=int(result["total_tracks"]),
        predicted_tracks_json=tracks_json,
    )
    return {
        "id": watchlist_id,
        "predicted_score": float(result["avg_score"]),
        "predicted_hits": int(result["hit_count"]),
        "predicted_total": int(result["total_tracks"]),
        "predicted_tracks_json": tracks_json,
        "matched_artist": result.get("matched_artist", entry["name"]),
    }


@app.post("/api/stc/sync")
def api_sync_stc_embeddings():
    """Starts incremental SonicTaste (STC) embedding & checkpoint refresh in the sidecar background."""
    return _call_stc_sidecar("/sync", {}, timeout=15, method="POST")


@app.get("/api/stc/sync/status")
def api_stc_sync_status():
    """Polls the status/result of the background SonicTaste (STC) embedding sync job."""
    return _call_stc_sidecar("/sync/status", timeout=15, method="GET")


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("KPOP_APP_PORT", "8000"))
    if database.IS_FROZEN:
        import threading
        import webbrowser

        threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
        uvicorn.run(app, host="127.0.0.1", port=port, reload=False)
    else:
        uvicorn.run("app:app", host="127.0.0.1", port=port, reload=True)