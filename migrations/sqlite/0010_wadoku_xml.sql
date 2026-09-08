PRAGMA foreign_keys=OFF;

CREATE TABLE source_snapshot_0010(
  id INTEGER PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('jitendex','kaishi','wadoku')),
  version TEXT NOT NULL, url TEXT NOT NULL, sha256 TEXT NOT NULL, local_path TEXT NOT NULL,
  extractor_version TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(kind, sha256)
);
INSERT INTO source_snapshot_0010 SELECT * FROM source_snapshot;
DROP TABLE source_snapshot;
ALTER TABLE source_snapshot_0010 RENAME TO source_snapshot;

CREATE TABLE run_0010(
  id INTEGER PRIMARY KEY,
  jitendex_snapshot_id INTEGER REFERENCES source_snapshot(id),
  kaishi_snapshot_id INTEGER REFERENCES source_snapshot(id),
  dictionary_snapshot_id INTEGER REFERENCES source_snapshot(id),
  selection_sha256 TEXT NOT NULL, extractor_version TEXT NOT NULL,
  prompt_sha256 TEXT NOT NULL, review_prompt_sha256 TEXT NOT NULL,
  terminology_sha256 TEXT NOT NULL, limits_json TEXT NOT NULL,
  pipeline_version TEXT NOT NULL DEFAULT 'scalar-v1',
  state TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  run_identity_sha256 TEXT UNIQUE CHECK(run_identity_sha256 IS NULL OR length(run_identity_sha256)=64),
  UNIQUE(jitendex_snapshot_id,kaishi_snapshot_id,selection_sha256,extractor_version,
         prompt_sha256,review_prompt_sha256,terminology_sha256,limits_json)
);
INSERT INTO run_0010(
  id,jitendex_snapshot_id,kaishi_snapshot_id,dictionary_snapshot_id,selection_sha256,
  extractor_version,prompt_sha256,review_prompt_sha256,terminology_sha256,limits_json,
  pipeline_version,state,created_at,run_identity_sha256
)
SELECT id,jitendex_snapshot_id,kaishi_snapshot_id,jitendex_snapshot_id,selection_sha256,
       extractor_version,prompt_sha256,review_prompt_sha256,terminology_sha256,limits_json,
       pipeline_version,state,created_at,NULL FROM run;
DROP TABLE run;
ALTER TABLE run_0010 RENAME TO run;

ALTER TABLE run_article ADD COLUMN prepared_at TEXT;
UPDATE run_article SET prepared_at=(SELECT created_at FROM run WHERE run.id=run_article.run_id)
WHERE prepared_at IS NULL;
ALTER TABLE attempt ADD COLUMN dispatched_at TEXT;
ALTER TABLE translation ADD COLUMN accepted_at TEXT;
ALTER TABLE translation ADD COLUMN acceptance_method TEXT
  CHECK(acceptance_method IS NULL OR acceptance_method IN ('luna','v1_exact_reuse','manual','legacy'));
UPDATE translation SET accepted_at=created_at,acceptance_method='legacy'
WHERE accepted=1 AND accepted_at IS NULL;

CREATE TABLE translation_reuse(
  target_translation_id INTEGER PRIMARY KEY REFERENCES translation(id),
  source_translation_id INTEGER NOT NULL REFERENCES translation(id),
  source_segment_index INTEGER NOT NULL,
  mapping_rule TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

PRAGMA foreign_keys=ON;

