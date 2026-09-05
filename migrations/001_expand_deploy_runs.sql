-- Additive migration for full web resume optimization history.
CREATE TABLE IF NOT EXISTS deploy_runs (
  run_id TEXT PRIMARY KEY,
  job_id TEXT UNIQUE NOT NULL,
  session_id TEXT NOT NULL,
  status TEXT NOT NULL,
  writer_prompt_version TEXT,
  hr_prompt_version TEXT,
  model_id TEXT,
  final_score INTEGER DEFAULT 0,
  iterations INTEGER DEFAULT 0,
  eval_metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
  final_result TEXT DEFAULT '',
  fabrication_report TEXT DEFAULT '',
  error_message TEXT DEFAULT '',
  created_at TIMESTAMPTZ DEFAULT NOW(),
  completed_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS deploy_agent_traces (
  trace_id BIGSERIAL PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES deploy_runs(run_id) ON UPDATE CASCADE,
  step_name TEXT,
  agent_name TEXT,
  iteration INTEGER DEFAULT 0,
  input_summary TEXT,
  output_summary TEXT,
  score INTEGER,
  latency_ms INTEGER DEFAULT 0,
  token_count INTEGER DEFAULT 0,
  status TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE deploy_runs ADD COLUMN IF NOT EXISTS fact_checker_prompt_version TEXT;
ALTER TABLE deploy_runs ADD COLUMN IF NOT EXISTS position_category TEXT DEFAULT '';
ALTER TABLE deploy_runs ADD COLUMN IF NOT EXISTS max_iterations INTEGER DEFAULT 3;
ALTER TABLE deploy_runs ADD COLUMN IF NOT EXISTS fabrication_tolerance INTEGER DEFAULT 0;
ALTER TABLE deploy_runs ADD COLUMN IF NOT EXISTS request_payload JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE deploy_runs ADD COLUMN IF NOT EXISTS iteration_history JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE deploy_runs ADD COLUMN IF NOT EXISTS familiarity JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE deploy_runs ADD COLUMN IF NOT EXISTS industry_glossary TEXT DEFAULT '';
ALTER TABLE deploy_runs ADD COLUMN IF NOT EXISTS badcase_labels JSONB NOT NULL DEFAULT '[]'::jsonb;
CREATE INDEX IF NOT EXISTS idx_deploy_runs_session ON deploy_runs(session_id, created_at DESC);
CREATE TABLE IF NOT EXISTS deploy_badcases (
  badcase_id BIGSERIAL PRIMARY KEY,
  run_id TEXT NOT NULL REFERENCES deploy_runs(run_id) ON UPDATE CASCADE,
  error_type TEXT NOT NULL,
  severity TEXT DEFAULT 'medium',
  details JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(run_id, error_type)
);
CREATE INDEX IF NOT EXISTS idx_deploy_badcases_run ON deploy_badcases(run_id);
