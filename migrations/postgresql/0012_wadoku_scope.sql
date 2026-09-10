CREATE TABLE wadoku_scope (
  id TEXT PRIMARY KEY,
  snapshot_id BIGINT NOT NULL REFERENCES source_snapshot(id),
  manifest_json TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE wadoku_scope_entry (
  scope_id TEXT NOT NULL REFERENCES wadoku_scope(id),
  entry_id BIGINT NOT NULL,
  ordinal BIGINT NOT NULL,
  categories_json TEXT NOT NULL,
  source_json TEXT NOT NULL,
  source_sha256 TEXT NOT NULL,
  decision_json TEXT,
  PRIMARY KEY(scope_id,entry_id)
);
CREATE TABLE wadoku_example_candidate (
  scope_id TEXT NOT NULL REFERENCES wadoku_scope(id),
  parent_id BIGINT NOT NULL,
  child_id BIGINT NOT NULL,
  relation_path TEXT NOT NULL,
  candidate_json TEXT NOT NULL,
  decision_json TEXT,
  PRIMARY KEY(scope_id,parent_id,child_id,relation_path)
);
