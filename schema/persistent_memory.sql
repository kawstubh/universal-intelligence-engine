-- SQLite schema for UIE persistent lesson memory.
-- PostgreSQL deployments should use an explicit migration; this file is SQLite-specific.
CREATE TABLE IF NOT EXISTS uie_lessons (
    id TEXT PRIMARY KEY,
    domain TEXT NOT NULL CHECK(length(trim(domain)) > 0),
    trigger_pattern TEXT NOT NULL,
    lesson_text TEXT NOT NULL CHECK(length(trim(lesson_text)) > 0),
    validation_status TEXT NOT NULL DEFAULT 'unverified_proposed'
        CHECK(validation_status IN
        ('unverified_proposed','doctor_validated','rejected','deprecated')),
    confidence REAL NOT NULL CHECK(confidence >= 0.0 AND confidence <= 1.0),
    source_origin TEXT NOT NULL,
    immutable_guardian_flag INTEGER NOT NULL DEFAULT 0
        CHECK(immutable_guardian_flag IN (0,1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    reviewer_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_uie_lessons_domain_status
    ON uie_lessons(domain, validation_status);
