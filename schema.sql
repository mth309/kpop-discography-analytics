PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS artists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    debut_year INTEGER,
    generation TEXT,
    status TEXT DEFAULT 'Active',
    artist_type TEXT DEFAULT 'group',
    parent_groups TEXT
);

CREATE TABLE IF NOT EXISTS albums (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    artist_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    release_date TEXT,
    album_type TEXT DEFAULT 'EP',
    mb_release_group_id TEXT,
    FOREIGN KEY (artist_id) REFERENCES artists (id) ON DELETE CASCADE,
    UNIQUE(artist_id, title, album_type)
);

CREATE TABLE IF NOT EXISTS tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    album_id INTEGER NOT NULL,
    track_number INTEGER,
    title TEXT NOT NULL,
    rating REAL CHECK(rating >= 1 AND rating <= 5),
    is_title_track BOOLEAN DEFAULT 0,
    track_type TEXT DEFAULT 'core',
    notes TEXT,
    FOREIGN KEY (album_id) REFERENCES albums (id) ON DELETE CASCADE,
    UNIQUE(album_id, title)
);

CREATE TABLE IF NOT EXISTS artist_members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL,
    member_name TEXT NOT NULL,
    solo_artist_id INTEGER,
    display_order INTEGER DEFAULT 1,
    is_former INTEGER DEFAULT 0,
    FOREIGN KEY (group_id) REFERENCES artists(id) ON DELETE CASCADE,
    FOREIGN KEY (solo_artist_id) REFERENCES artists(id) ON DELETE SET NULL,
    UNIQUE (group_id, member_name)
);

CREATE TABLE IF NOT EXISTS artist_watchlist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    artist_type TEXT DEFAULT 'group',
    generation TEXT,
    status TEXT DEFAULT 'Active',
    parent_groups TEXT,
    members TEXT,
    predicted_score REAL,
    predicted_hits INTEGER,
    predicted_total INTEGER,
    predicted_tracks_json TEXT,
    predicted_at TEXT,
    date_added TEXT,
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_albums_artist_id ON albums(artist_id);
CREATE INDEX IF NOT EXISTS idx_tracks_album_id ON tracks(album_id);
CREATE INDEX IF NOT EXISTS idx_artist_members_group_id ON artist_members(group_id);
CREATE INDEX IF NOT EXISTS idx_artist_members_solo_id ON artist_members(solo_artist_id);
CREATE INDEX IF NOT EXISTS idx_artist_watchlist_type ON artist_watchlist(artist_type);
