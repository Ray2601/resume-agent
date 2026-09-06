-- Additive migration for real pipeline stage progress.
ALTER TABLE deploy_runs ADD COLUMN IF NOT EXISTS current_stage TEXT DEFAULT '';
ALTER TABLE deploy_runs ADD COLUMN IF NOT EXISTS current_stage_label TEXT DEFAULT '';
ALTER TABLE deploy_runs ADD COLUMN IF NOT EXISTS stage_status TEXT DEFAULT 'pending';
ALTER TABLE deploy_runs ADD COLUMN IF NOT EXISTS progress_percent INTEGER DEFAULT 0;
ALTER TABLE deploy_runs ADD COLUMN IF NOT EXISTS current_iteration INTEGER DEFAULT 0;
ALTER TABLE deploy_runs ADD COLUMN IF NOT EXISTS total_iterations INTEGER DEFAULT 3;
ALTER TABLE deploy_runs ADD COLUMN IF NOT EXISTS progress_message TEXT DEFAULT '';
ALTER TABLE deploy_runs ADD COLUMN IF NOT EXISTS stage_started_at TIMESTAMPTZ;
ALTER TABLE deploy_runs ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMPTZ;
ALTER TABLE deploy_runs ADD COLUMN IF NOT EXISTS failed_stage TEXT DEFAULT '';
ALTER TABLE deploy_runs ADD COLUMN IF NOT EXISTS failed_agent TEXT DEFAULT '';
CREATE TABLE IF NOT EXISTS deploy_run_stages (
  id BIGSERIAL PRIMARY KEY, job_id TEXT NOT NULL, session_id TEXT NOT NULL,
  stage_key TEXT NOT NULL, stage_label TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending', iteration INTEGER DEFAULT 0,
  message TEXT DEFAULT '', started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ, error_message TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_deploy_run_stages_job ON deploy_run_stages(job_id, id);
