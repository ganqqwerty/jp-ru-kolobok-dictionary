-- Keep derived rich entries separate from immutable source articles.
CREATE TABLE wadoku_projection (
  run_id BIGINT NOT NULL,
  article_id BIGINT NOT NULL,
  context_sha256 TEXT NOT NULL CHECK (length(context_sha256)=64),
  projection_json TEXT NOT NULL,
  PRIMARY KEY (run_id, article_id),
  FOREIGN KEY (run_id, article_id) REFERENCES run_article(run_id, article_id)
);

-- A window is a bounded set of root batches, not a submission counter.
CREATE TABLE wadoku_window (
  run_id BIGINT NOT NULL REFERENCES run(id),
  ordinal BIGINT NOT NULL CHECK (ordinal > 0),
  state TEXT NOT NULL CHECK (state IN ('running','awaiting_analysis','continue','repair','stopped')),
  batch_ids_json TEXT NOT NULL,
  report_json TEXT NOT NULL DEFAULT '{}',
  analysis_json TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (run_id, ordinal)
);
CREATE UNIQUE INDEX wadoku_one_active_window ON wadoku_window(run_id)
WHERE state IN ('running','awaiting_analysis','repair');
