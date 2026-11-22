"""MusicBrainz API Ingestion and Track Classification Engine.

Handles communication with MusicBrainz Web Service (ws/2), rate limiting,
candidate scoring, tracklist parsing, and heuristic track-type classification
under the standard 5-tier track model.
"""

import os
import time
from typing import Any, Dict, List, Optional, Tuple
import re
import requests

HEADERS = {
    "User-Agent": os.environ.get(
        "MB_USER_AGENT",
        "KPopRatingTracker/1.0.0 ( https://github.com/kpop-rating-tracker )",
    ),
    "Accept": "application/json",
}

BASE_URL = "https://musicbrainz.org/ws/2"

# Regex patterns for automatic track type categorization (5-tier model)
NON_CANON_PATTERN = re.compile(
    r"\b(inst\.?|instrumental|karaoke|backing\s*track|interlude|intro|outro|skit|prologue|epilogue|spoiled|remix|re-mix|mix|acoustic|cappella|club|sped\s*up|sped-up|slowed)\b",
    re.IGNORECASE
)

# Modular language and edition tokens for robust alt_lang detection
_LANG_TOKENS = r"(?:japan(?:ese)?|jap|jpn|jp|english|eng|en|chinese|chn|chi|zh|korean|korea|kor|kr|spanish)"
_EDITION_TOKENS = r"(?:ver(?:s(?:ion)?)?|ed(?:ition)?|rel(?:ease)?)"

ALT_LANG_PATTERN = re.compile(
    # Pattern A: Lang + Edition (e.g. 'Japanese ed.', 'JP-ver', 'English version', 'Kor. rel')
    rf"\b{_LANG_TOKENS}\.?\s*[-/]?\s*{_EDITION_TOKENS}\b|"
    # Pattern B: Edition + Lang (e.g. 'Version Japanese', 'Ver. Eng')
    rf"\b{_EDITION_TOKENS}\.?\s*[-/]?\s*{_LANG_TOKENS}\b|"
    # Pattern C: Standalone Lang enclosed in brackets or hyphens (e.g. '(JP)', '[ENG]', '-Japanese-', '(Korean)')
    r"(?:[\(\[\{]|\s+-\s*)" + _LANG_TOKENS + r"\.?(?:[\)\]\}]|\s*-\s*|\s*$)",
    re.IGNORECASE
)

TRACK_TYPE_PATTERNS = {
    "non_canon": NON_CANON_PATTERN,
    "alt_lang": ALT_LANG_PATTERN,
}

session = requests.Session()
session.headers.update(HEADERS)


def _make_request(url: str, params: Optional[Dict[str, Any]] = None, max_retries: int = 4, base_delay: float = 1.2) -> dict:
    """Executes a request to MusicBrainz using a persistent session.

    Enforces rate-limit delays between queries and performs exponential
    backoff retry handling for HTTP 503/429 rate limits, connection drops,
    and SSL resets.

    Args:
        url: Full destination URL string.
        params: Optional query parameters dictionary.
        max_retries: Maximum number of request attempts before giving up.
        base_delay: Minimum delay in seconds before each attempt.

    Returns:
        dict: Parsed JSON payload from the MusicBrainz response.

    Raises:
        RuntimeError: If MusicBrainz remains unavailable after max_retries.
        requests.RequestException: If an unrecoverable request error occurs.
    """
    for attempt in range(max_retries):
        time.sleep(base_delay)
        try:
            response = session.get(url, params=params, timeout=15)
            
            if response.status_code in (503, 429):
                wait_time = (attempt + 1) * 3.0
                print(f"[MusicBrainz] Rate limited ({response.status_code}). Waiting {wait_time:.1f}s...")
                time.sleep(wait_time)
                continue
                
            response.raise_for_status()
            return response.json()
            
        except (requests.exceptions.SSLError, requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            wait_time = (attempt + 1) * 3.5
            print(f"[MusicBrainz] Connection drop / SSL reset. Retrying in {wait_time:.1f}s (Attempt {attempt + 1}/{max_retries})...")
            time.sleep(wait_time)
            if attempt == max_retries - 1:
                raise e
        except requests.RequestException as e:
            if attempt == max_retries - 1:
                raise e
            time.sleep(base_delay)
            
    raise RuntimeError("MusicBrainz service unavailable after retries.")


def normalize_title_quotes(text: Optional[str]) -> str:
    """Normalizes typographic single quotes/apostrophes to straight ASCII apostrophes."""
    if not text:
        return ""
    return re.sub(r"[\u2018\u2019]", "'", text).strip()


def detect_track_type(title: str) -> str:
    """Infers the track type from its title using keyword and edition heuristics.

    Evaluates titles against the application's 5-tier classification model
    with strict precedence: non_canon -> alt_lang -> core.

    Tracks containing both remix/skit keywords and language tags (e.g.,
    "DDU-DU DDU-DU (remix) -JP ver.-") evaluate strictly to 'non_canon'.

    Args:
        title: Raw track title string.

    Returns:
        str: Classification tier ('non_canon', 'alt_lang', or 'core').
    """
    if not title:
        return "core"

    # 1. Non-canon takes strict precedence (remixes, instrumentals, skits, intros, outros, etc.)
    if TRACK_TYPE_PATTERNS["non_canon"].search(title):
        return "non_canon"

    # 2. Alt-lang detection (modular Lang+Edition or standalone enclosed lang)
    if TRACK_TYPE_PATTERNS["alt_lang"].search(title):
        return "alt_lang"

    return "core"


def search_artist_id(artist_name: str) -> Optional[str]:
    """Finds the MusicBrainz Artist MBID for a given artist name.

    If artist_name is already a valid 36-character UUID, it is returned directly.
    Otherwise queries MusicBrainz with exact phrase matching and fallback
    relaxation, prioritizing Korean domestic artists ('KR') and groups.

    Args:
        artist_name: Plaintext artist name or 36-character MBID string.

    Returns:
        Optional[str]: MusicBrainz artist UUID if resolved, otherwise None.
    """
    cleaned = (artist_name or "").strip()
    if not cleaned:
        return None

    if len(cleaned) == 36 and cleaned.count("-") == 4:
        return cleaned

    # Escape Lucene special characters or try fuzzy fallback if exact phrase yields 0
    clean_query = requests.utils.quote(cleaned)
    url = f'{BASE_URL}/artist/?query=artist:"{clean_query}"&fmt=json'
    data = _make_request(url)
    artists = data.get("artists", [])
    
    if not artists:
        # Fallback without quotes and without slashes (e.g., "H PE Princess" or "HYPE Princess")
        relaxed = re.sub(r"[/\\+!^~*?:()]", " ", cleaned).strip()
        url = f"{BASE_URL}/artist/?query={requests.utils.quote(relaxed)}&fmt=json"
        data = _make_request(url)
        artists = data.get("artists", [])
        if not artists:
            return None

    exact_matches = [
        a for a in artists 
        if (a.get("name") or "").lower() == cleaned.lower() 
        or (a.get("sort-name") or "").lower() == cleaned.lower()
    ]
    candidates = exact_matches if exact_matches else artists

    korean_artist = next((a for a in candidates if a.get("country") == "KR"), None)
    if korean_artist:
        return korean_artist["id"]

    group_artist = next((a for a in candidates if a.get("type") == "Group"), None)
    if group_artist:
        return group_artist["id"]

    return candidates[0]["id"]


def _score_release_candidate(r: Dict[str, Any], max_tracks: int = 0) -> Tuple[int, int, int, int]:
    """Scores candidate release variants to choose the most definitive release.

    Prioritizes:
    1. Official release status (avoids pseudo-releases or bootlegs if official exists).
    2. Track count preservation: heavily penalizes promotional samplers or partial releases
       that contain fewer tracks than the release group's maximum track count.
    3. Track count: higher track count preferred to ensure complete tracklists.
    4. Language & Script: prefers Latin script ('Latn') and English ('eng') over purely Hangul
       releases to stage accessible English or dual-language titles without manual translation.
    5. International distribution: prefers Worldwide ('XW'), US, GB, etc. over domestic 'KR'
       when Latin/English metadata is present, falling back to 'KR' when no international
       edition exists.

    Args:
        r: MusicBrainz release record dictionary.
        max_tracks: Optional maximum track count among candidates in the release group.

    Returns:
        Tuple[int, int, int, int]: (status_score, track_penalty, track_count, lang_score)
        tuple suitable for tuple comparison / sorting.
    """
    status = (r.get("status") or "").lower()
    status_score = 3 if status == "official" else (1 if status == "pseudo-release" else 0)

    track_count = r.get("track-count", 0) or 0
    track_penalty = 10 if (max_tracks > 0 and track_count >= max_tracks) else (0 if max_tracks == 0 else -10)

    text_rep = r.get("text-representation") or {}
    script = (text_rep.get("script") or "").capitalize()
    lang = (text_rep.get("language") or "").lower()

    # Script & Language score
    script_score = 4 if script == "Latn" else 0
    lang_score = 2 if lang == "eng" else 0

    # Country score: Worldwide and major international markets take precedence over domestic KR
    country = (r.get("country") or "").upper()
    if country in ("XW", "US", "GB", "CA", "AU"):
        country_score = 3
    elif country == "KR":
        country_score = 1
    else:
        country_score = 0

    total_lang_score = script_score + lang_score + country_score
    return (status_score, track_penalty, track_count, total_lang_score)


def _pick_best_release(releases: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Evaluates candidate releases using scoring heuristic and returns the optimal variant.

    Args:
        releases: List of release candidate dictionaries from MusicBrainz.

    Returns:
        Optional[Dict[str, Any]]: Best matching release record, or None if empty.
    """
    if not releases:
        return None
    max_tracks = max((r.get("track-count", 0) or 0) for r in releases)
    return max(releases, key=lambda r: _score_release_candidate(r, max_tracks=max_tracks))


def search_release(artist: str, album: str) -> Optional[Dict[str, Any]]:
    """Searches MusicBrainz for releases matching an artist and album title.

    Args:
        artist: Artist name to search.
        album: Album title to search.

    Returns:
        Optional[Dict[str, Any]]: Dictionary containing:
            - mbid (str): Release UUID.
            - title (str): Album title.
            - artist (str): Primary artist credit name.
            - date (str): First release date (YYYY-MM-DD or partial).
            - country (str): 2-letter country code.
            - track_count (int): Total track count.
            Returns None if no matching release is found.
    """
    query = f'artist:"{artist}" AND release:"{album}"'
    url = f"{BASE_URL}/release/"
    
    data = _make_request(url, params={"query": query, "fmt": "json"})
    
    releases = data.get("releases", [])
    best = _pick_best_release(releases)
    if not best:
        return None
        
    return {
        "mbid": best["id"],
        "title": normalize_title_quotes(best.get("title")),
        "artist": best.get("artist-credit", [{}])[0].get("name", artist),
        "date": best.get("date", ""),
        "country": best.get("country", ""),
        "track_count": best.get("track-count", 0),
    }


def get_album_tracklist(mbid: str, fallback_artist: Optional[str] = None, rg_id: Optional[str] = None) -> Dict[str, Any]:
    """Fetches full album metadata and parsed tracklist from a MusicBrainz release UUID.

    Filters out video formats (DVD, Blu-ray) to avoid duplicate audio track entries
    and infers track types via `detect_track_type()`.

    Args:
        mbid: MusicBrainz release UUID.
        fallback_artist: Optional artist name to use if artist-credit is absent.
        rg_id: Optional release-group UUID override.

    Returns:
        Dict[str, Any]: Structured album payload ready for staging:
            - artist (str): Artist name.
            - title (str): Album title.
            - release_date (str): Release date string.
            - album_type (str): 'EP', 'LP', or 'Single'.
            - mb_release_group_id (str): MusicBrainz release-group UUID.
            - tracks (List[Dict[str, Any]]): List of track dictionaries with keys
              track_number, title, is_title_track, track_type, notes, rating.
    """
    url = f"{BASE_URL}/release/{mbid}?inc=recordings+release-groups+artist-credits&fmt=json"
    data = _make_request(url)
    
    release_group = data.get("release-group", {})
    actual_rg_id = rg_id or release_group.get("id")
    primary_type = release_group.get("primary-type", "EP")
    if primary_type == "Album":
        primary_type = "LP"

    album_title = normalize_title_quotes(data.get("title", ""))
    artist_credits = data.get("artist-credit", [])
    artist_name = artist_credits[0].get("name") if artist_credits else fallback_artist

    tracks = []
    seen_titles = set()

    # Prioritize audio CD media to avoid duplicate DVD track titles
    media_list = data.get("media", [])
    audio_media = [m for m in media_list if m.get("format") not in ("DVD", "DVD-Video", "Blu-ray")]
    selected_media = audio_media if audio_media else media_list

    for media in selected_media:
        for track in media.get("tracks", []):
            t_title = normalize_title_quotes(track.get("title", ""))
            if not t_title or t_title.lower() in seen_titles:
                continue
            seen_titles.add(t_title.lower())

            tracks.append({
                "track_number": len(tracks) + 1,
                "title": t_title,
                "is_title_track": False,
                "track_type": detect_track_type(t_title),
                "notes": None,
                "rating": None
            })

    return {
        "artist": artist_name,
        "title": album_title,
        "release_date": data.get("date"),
        "album_type": primary_type,
        "mb_release_group_id": actual_rg_id,
        "tracks": tracks
    }


def fetch_album_metadata(artist: str, album: str) -> Optional[Dict[str, Any]]:
    """Convenience helper combining release search and tracklist retrieval for single import.

    Args:
        artist: Artist name string.
        album: Album title string.

    Returns:
        Optional[Dict[str, Any]]: Structured album payload or None if not found.
    """
    search_res = search_release(artist, album)
    if not search_res:
        return None
    return get_album_tracklist(search_res["mbid"], fallback_artist=artist)


def get_artist_discography(artist_name: str) -> List[Dict[str, Any]]:
    """Retrieves all official release groups for an artist from MusicBrainz.

    Excludes live albums, remix packages, compilation albums, and mixtapes,
    returning official core releases sorted chronologically.

    Args:
        artist_name: Artist name or MBID to look up.

    Returns:
        List[Dict[str, Any]]: List of release group summary dicts with keys:
            - rg_id (str): Release group UUID.
            - title (str): Release title.
            - release_date (str): First release date.
            - primary_type (str): 'EP', 'LP', or 'Single'.
    """
    artist_id = search_artist_id(artist_name)
    if not artist_id:
        return []

    url = f"{BASE_URL}/release-group?artist={artist_id}&limit=100&fmt=json"
    data = _make_request(url)
    
    release_groups = data.get("release-groups", [])
    discography = []
    seen_titles = set()

    IGNORED_SECONDARY = {"live", "remix", "compilation", "dj-mix", "mixtape/street"}

    for rg in release_groups:
        secondary_types = {st.lower() for st in rg.get("secondary-types", []) if st}
        if secondary_types & IGNORED_SECONDARY:
            continue

        title = (rg.get("title") or "").strip()
        norm_title = title.lower()
        if not norm_title or norm_title in seen_titles:
            continue
        seen_titles.add(norm_title)

        primary_type = rg.get("primary-type") or "EP"
        if primary_type == "Album":
            primary_type = "LP"

        discography.append({
            "rg_id": rg["id"],
            "title": title,
            "release_date": rg.get("first-release-date", "") or "",
            "primary_type": primary_type
        })

    discography.sort(key=lambda x: x["release_date"] or "9999-99-99")
    return discography


def get_tracklist_for_release_group(rg_id: str, fallback_artist: str) -> Optional[Dict[str, Any]]:
    """Resolves the best candidate release for a release group and retrieves its tracklist.

    Used during bulk discography import queues to resolve tracklists on-demand.

    Args:
        rg_id: MusicBrainz release-group UUID.
        fallback_artist: Artist name to fall back on if credits are missing.

    Returns:
        Optional[Dict[str, Any]]: Structured album payload or None if unresolvable.
    """
    url = f"{BASE_URL}/release?release-group={rg_id}&limit=15&fmt=json"
    data = _make_request(url)
    releases = data.get("releases", [])
    best = _pick_best_release(releases)
    if not best:
        return None

    return get_album_tracklist(best["id"], fallback_artist=fallback_artist, rg_id=rg_id)
