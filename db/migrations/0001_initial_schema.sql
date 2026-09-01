-- ============================================================================
-- 0001_initial_schema.sql
-- Production Roadmap Phase 0 Step 0.4 — the frozen session database schema.
--
-- 10 substantive tables (sessions, messages, session_chunks, reminders,
-- todos, meeting_notes, schedules, schedule_items, summaries,
-- sync_metadata) + schema_migrations (infra, not one of the 10). Every
-- substantive table has id/created_at/updated_at/deleted_at plus a
-- nullable sync_metadata JSON column (inert in v1, reserved for v2 cloud
-- sync), except sync_metadata itself — a self-referential JSON column on
-- the sync-bookkeeping table would be meaningless.
--
-- Design rationale for every deliberate deviation from a literal reading
-- of production_roadmap.md's per-table column lists is recorded in
-- docs/schema_review.md's "Resolved Gaps" section — read that alongside
-- this file, not just this file alone.
-- ============================================================================

PRAGMA foreign_keys = ON;

-- ============================================================
-- sessions
-- ============================================================
CREATE TABLE sessions (
    id              TEXT PRIMARY KEY,
    started_at      TEXT NOT NULL,
    ended_at        TEXT,
    close_reason    TEXT CHECK (close_reason IN ('idle_timeout', 'explicit', 'app_shutdown') OR close_reason IS NULL),
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    deleted_at      TEXT,
    sync_metadata   TEXT
);
CREATE INDEX idx_sessions_started_at ON sessions(started_at);

-- ============================================================
-- messages
-- ============================================================
CREATE TABLE messages (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES sessions(id),
    turn_index      INTEGER NOT NULL,      -- 0-based position within the session
    role            TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content         TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    deleted_at      TEXT,
    sync_metadata   TEXT,
    UNIQUE (session_id, turn_index)
);
CREATE INDEX idx_messages_session_id ON messages(session_id);

-- ============================================================
-- session_chunks  (vector store — brute-force cosine over an in-memory
-- numpy array loaded from this table at startup; see project_logic.md §5)
-- ============================================================
CREATE TABLE session_chunks (
    id                          TEXT PRIMARY KEY,
    session_id                  TEXT NOT NULL REFERENCES sessions(id),
    chunk_type                  TEXT NOT NULL CHECK (chunk_type IN ('primary', 'sub_chunk')),
    window_start_message_idx    INTEGER,        -- NULL for primary chunks (covers whole session)
    window_end_message_idx      INTEGER,        -- NULL for primary chunks
    content                     TEXT NOT NULL,  -- raw text
    embedding                   BLOB NOT NULL,  -- raw float32 array, 384-dim (all-MiniLM-L6-v2)
    token_count                 INTEGER NOT NULL,
    topics                      TEXT NOT NULL DEFAULT '[]',  -- JSON array
    action_types                TEXT NOT NULL DEFAULT '[]',  -- JSON array
    entities                    TEXT NOT NULL DEFAULT '[]',  -- JSON array
    sentiment                   TEXT NOT NULL DEFAULT '',
    -- Denormalized (not derived via a messages join at query time): every
    -- other SessionRetrievedChunk-derived field above is persisted rather
    -- than recomputed per-retrieval, and the <200ms vector-search budget
    -- can't afford a join at scale either. See docs/schema_review.md.
    message_roles                TEXT NOT NULL DEFAULT '[]',  -- JSON array
    created_at                    TEXT NOT NULL,
    updated_at                    TEXT NOT NULL,
    deleted_at                    TEXT,
    sync_metadata                 TEXT
);
CREATE INDEX idx_session_chunks_session_id ON session_chunks(session_id);
-- Enforces "one primary chunk per session" — SessionRetrievedChunk.parent_chunk_id
-- is derivable from this at query time (session_id + chunk_type='primary')
-- rather than needing its own FK column.
CREATE UNIQUE INDEX idx_session_chunks_primary ON session_chunks(session_id)
    WHERE chunk_type = 'primary' AND deleted_at IS NULL;

-- ============================================================
-- reminders  (+ FTS5)
-- ============================================================
CREATE TABLE reminders (
    id              TEXT PRIMARY KEY,
    session_id      TEXT REFERENCES sessions(id),
    title           TEXT NOT NULL,
    notes           TEXT NOT NULL DEFAULT '',
    scheduled_time  TEXT NOT NULL,
    fired_at        TEXT,
    completed_at    TEXT,
    dismissed_at    TEXT,
    toast_id        TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    deleted_at      TEXT,
    sync_metadata   TEXT
);
CREATE INDEX idx_reminders_scheduled_time ON reminders(scheduled_time);

CREATE VIRTUAL TABLE reminders_fts USING fts5(
    title, notes, content='reminders', content_rowid='rowid'
);
CREATE TRIGGER reminders_ai AFTER INSERT ON reminders BEGIN
    INSERT INTO reminders_fts(rowid, title, notes) VALUES (new.rowid, new.title, new.notes);
END;
CREATE TRIGGER reminders_ad AFTER DELETE ON reminders BEGIN
    INSERT INTO reminders_fts(reminders_fts, rowid, title, notes) VALUES ('delete', old.rowid, old.title, old.notes);
END;
CREATE TRIGGER reminders_au AFTER UPDATE ON reminders BEGIN
    INSERT INTO reminders_fts(reminders_fts, rowid, title, notes) VALUES ('delete', old.rowid, old.title, old.notes);
    INSERT INTO reminders_fts(rowid, title, notes) VALUES (new.rowid, new.title, new.notes);
END;

-- ============================================================
-- todos  (+ FTS5)
-- ============================================================
CREATE TABLE todos (
    id              TEXT PRIMARY KEY,
    session_id      TEXT REFERENCES sessions(id),
    title           TEXT NOT NULL,
    notes           TEXT NOT NULL DEFAULT '',
    priority        TEXT CHECK (priority IN ('low', 'medium', 'high') OR priority IS NULL),
    category        TEXT,
    completed_at    TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    deleted_at      TEXT,
    sync_metadata   TEXT
);
CREATE VIRTUAL TABLE todos_fts USING fts5(title, notes, content='todos', content_rowid='rowid');
CREATE TRIGGER todos_ai AFTER INSERT ON todos BEGIN
    INSERT INTO todos_fts(rowid, title, notes) VALUES (new.rowid, new.title, new.notes);
END;
CREATE TRIGGER todos_ad AFTER DELETE ON todos BEGIN
    INSERT INTO todos_fts(todos_fts, rowid, title, notes) VALUES ('delete', old.rowid, old.title, old.notes);
END;
CREATE TRIGGER todos_au AFTER UPDATE ON todos BEGIN
    INSERT INTO todos_fts(todos_fts, rowid, title, notes) VALUES ('delete', old.rowid, old.title, old.notes);
    INSERT INTO todos_fts(rowid, title, notes) VALUES (new.rowid, new.title, new.notes);
END;

-- ============================================================
-- meeting_notes  (+ FTS5)
-- ============================================================
CREATE TABLE meeting_notes (
    id                TEXT PRIMARY KEY,
    session_id        TEXT REFERENCES sessions(id),
    raw_transcript    TEXT NOT NULL,
    attendees         TEXT NOT NULL DEFAULT '[]',   -- JSON array
    topics            TEXT NOT NULL DEFAULT '[]',   -- JSON array
    decisions         TEXT NOT NULL DEFAULT '[]',   -- JSON array
    action_items      TEXT NOT NULL DEFAULT '[]',   -- JSON array of {task, owner, deadline}
    follow_ups        TEXT NOT NULL DEFAULT '[]',   -- JSON array
    needs_review      INTEGER NOT NULL DEFAULT 0,   -- 0/1 -- set when neither decisions nor action_items is non-empty
    -- Flattened attendees+topics+decisions+action_items+follow_ups, written
    -- by the APPLICATION layer (Phase 1 Step 1.5's
    -- MeetingNoteHandler.capture_meeting_note()), never by a trigger or a
    -- generated column -- the FTS5 triggers below only ever read this
    -- column as already-flattened text. Exists purely so FTS5 has flat
    -- text to index without doing JSON-array flattening inside a SQL
    -- trigger. See docs/schema_review.md.
    searchable_text    TEXT NOT NULL DEFAULT '',
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    deleted_at         TEXT,
    sync_metadata      TEXT
);
CREATE VIRTUAL TABLE meeting_notes_fts USING fts5(raw_transcript, searchable_text, content='meeting_notes', content_rowid='rowid');
CREATE TRIGGER meeting_notes_ai AFTER INSERT ON meeting_notes BEGIN
    INSERT INTO meeting_notes_fts(rowid, raw_transcript, searchable_text) VALUES (new.rowid, new.raw_transcript, new.searchable_text);
END;
CREATE TRIGGER meeting_notes_ad AFTER DELETE ON meeting_notes BEGIN
    INSERT INTO meeting_notes_fts(meeting_notes_fts, rowid, raw_transcript, searchable_text) VALUES ('delete', old.rowid, old.raw_transcript, old.searchable_text);
END;
CREATE TRIGGER meeting_notes_au AFTER UPDATE ON meeting_notes BEGIN
    INSERT INTO meeting_notes_fts(meeting_notes_fts, rowid, raw_transcript, searchable_text) VALUES ('delete', old.rowid, old.raw_transcript, old.searchable_text);
    INSERT INTO meeting_notes_fts(rowid, raw_transcript, searchable_text) VALUES (new.rowid, new.raw_transcript, new.searchable_text);
END;

-- ============================================================
-- schedules
-- ============================================================
CREATE TABLE schedules (
    id              TEXT PRIMARY KEY,
    date            TEXT NOT NULL,     -- ISO date this schedule applies to
    title           TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    deleted_at      TEXT,
    sync_metadata   TEXT
);
CREATE INDEX idx_schedules_date ON schedules(date);

-- ============================================================
-- schedule_items  (+ FTS5)
-- ============================================================
CREATE TABLE schedule_items (
    id              TEXT PRIMARY KEY,
    schedule_id     TEXT NOT NULL REFERENCES schedules(id),
    title           TEXT NOT NULL,
    start_time      TEXT NOT NULL,
    end_time        TEXT NOT NULL,
    location        TEXT NOT NULL DEFAULT '',
    notes           TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    deleted_at      TEXT,
    sync_metadata   TEXT
);
CREATE INDEX idx_schedule_items_schedule_id ON schedule_items(schedule_id);
CREATE VIRTUAL TABLE schedule_items_fts USING fts5(title, notes, location, content='schedule_items', content_rowid='rowid');
CREATE TRIGGER schedule_items_ai AFTER INSERT ON schedule_items BEGIN
    INSERT INTO schedule_items_fts(rowid, title, notes, location) VALUES (new.rowid, new.title, new.notes, new.location);
END;
CREATE TRIGGER schedule_items_ad AFTER DELETE ON schedule_items BEGIN
    INSERT INTO schedule_items_fts(schedule_items_fts, rowid, title, notes, location) VALUES ('delete', old.rowid, old.title, old.notes, old.location);
END;
CREATE TRIGGER schedule_items_au AFTER UPDATE ON schedule_items BEGIN
    INSERT INTO schedule_items_fts(schedule_items_fts, rowid, title, notes, location) VALUES ('delete', old.rowid, old.title, old.notes, old.location);
    INSERT INTO schedule_items_fts(rowid, title, notes, location) VALUES (new.rowid, new.title, new.notes, new.location);
END;

-- ============================================================
-- summaries
-- ============================================================
CREATE TABLE summaries (
    id              TEXT PRIMARY KEY,
    summary_type    TEXT NOT NULL CHECK (summary_type IN ('daily', 'weekly')),
    period_start    TEXT NOT NULL,      -- ISO date
    period_end      TEXT NOT NULL,      -- == period_start for daily
    content         TEXT NOT NULL,
    scheduled_at    TEXT NOT NULL,      -- the intended generation time -- used even if generated late
    generated_at    TEXT NOT NULL,      -- actual generation time
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    deleted_at      TEXT,
    sync_metadata   TEXT
);
-- Idempotency: regeneration overwrites the active row for the same period
-- rather than duplicating it. A PARTIAL unique index (not a plain UNIQUE
-- constraint) so a soft-deleted historical summary never blocks
-- regeneration for that period.
CREATE UNIQUE INDEX idx_summaries_period ON summaries(summary_type, period_start)
    WHERE deleted_at IS NULL;

-- ============================================================
-- sync_metadata  (present-but-inert in v1, reserved for v2 cloud sync)
-- ============================================================
CREATE TABLE sync_metadata (
    id              TEXT PRIMARY KEY,
    table_name      TEXT NOT NULL,
    row_id          TEXT NOT NULL,
    last_synced_at  TEXT,
    sync_version    INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    deleted_at      TEXT
    -- Deliberately has no sync_metadata column of its own -- a
    -- self-referential JSON column on the table that *is* the sync
    -- bookkeeping mechanism is nonsensical. Documented exception to the
    -- universal-constraint rule (see docs/schema_review.md).
);

-- ============================================================
-- schema_migrations  (infra, not one of the 10 substantive tables)
-- ============================================================
CREATE TABLE schema_migrations (
    version      TEXT PRIMARY KEY,
    filename     TEXT NOT NULL,
    checksum     TEXT NOT NULL,
    applied_at   TEXT NOT NULL
);
