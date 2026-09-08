ALTER TABLE source_snapshot DROP CONSTRAINT source_snapshot_kind_check;
ALTER TABLE source_snapshot ADD CONSTRAINT source_snapshot_kind_check
  CHECK (kind IN ('jitendex', 'kaishi', 'wadoku'));

ALTER TABLE run ADD COLUMN dictionary_snapshot_id BIGINT REFERENCES source_snapshot(id);
ALTER TABLE run ADD COLUMN run_identity_sha256 TEXT;
ALTER TABLE run ALTER COLUMN jitendex_snapshot_id DROP NOT NULL;
ALTER TABLE run ALTER COLUMN kaishi_snapshot_id DROP NOT NULL;
UPDATE run SET dictionary_snapshot_id=jitendex_snapshot_id
WHERE dictionary_snapshot_id IS NULL AND jitendex_snapshot_id IS NOT NULL;
ALTER TABLE run ADD CONSTRAINT run_identity_sha256_format
  CHECK (run_identity_sha256 IS NULL OR length(run_identity_sha256)=64);
CREATE UNIQUE INDEX run_identity_sha256_unique ON run(run_identity_sha256)
WHERE run_identity_sha256 IS NOT NULL;

ALTER TABLE run_article ADD COLUMN prepared_at TIMESTAMPTZ;
UPDATE run_article ra SET prepared_at=r.created_at FROM run r
WHERE r.id=ra.run_id AND ra.prepared_at IS NULL;

ALTER TABLE attempt ADD COLUMN dispatched_at TIMESTAMPTZ;

ALTER TABLE translation ADD COLUMN accepted_at TIMESTAMPTZ;
ALTER TABLE translation ADD COLUMN acceptance_method TEXT;
UPDATE translation SET accepted_at=created_at,acceptance_method='legacy'
WHERE accepted=1 AND accepted_at IS NULL;
ALTER TABLE translation ADD CONSTRAINT translation_acceptance_method_check
  CHECK (acceptance_method IS NULL OR acceptance_method IN ('luna','v1_exact_reuse','manual','legacy'));

CREATE TABLE translation_reuse(
  target_translation_id BIGINT PRIMARY KEY REFERENCES translation(id),
  source_translation_id BIGINT NOT NULL REFERENCES translation(id),
  source_segment_index BIGINT NOT NULL,
  mapping_rule TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

