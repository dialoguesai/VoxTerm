CREATE TABLE IF NOT EXISTS transcripts (
    session_id        TEXT PRIMARY KEY,
    hostname          TEXT,
    model_name        TEXT,
    language          TEXT,
    started_at        TEXT,
    ended_at          TEXT,
    entry_count       INTEGER,
    voxterm_version   TEXT,
    uploaded_at       TEXT NOT NULL,
    transcript_path   TEXT NOT NULL,
    audio_path        TEXT,
    audio_bytes       INTEGER
);

CREATE INDEX IF NOT EXISTS idx_transcripts_uploaded_at ON transcripts(uploaded_at DESC);
CREATE INDEX IF NOT EXISTS idx_transcripts_hostname ON transcripts(hostname);
