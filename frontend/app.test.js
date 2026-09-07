const assert = require("node:assert/strict");
const { mergeUploadedFiles, appendText, normalizeRunStatus, statusIcon } = require("./app.js");

(async () => {
  const files = [
    { name: "one.md", text: async () => "first resume" },
    { name: "two.txt", text: async () => "second resume" },
    { name: "ignored.pdf", text: async () => "ignore" },
  ];
  const merged = await mergeUploadedFiles(files, "REFERENCE RESUME");
  assert.match(merged, /REFERENCE RESUME: one\.md/);
  assert.match(merged, /REFERENCE RESUME: two\.txt/);
  assert.doesNotMatch(merged, /ignored/);
  assert.equal(appendText("a", "b"), "a\n\nb");
  const completed = normalizeRunStatus({
    status: "completed",
    progress_percent: 72,
    stage_status: "running",
    stages: [{ key: "phase3_fabrication_audit", status: "running" }],
  });
  assert.equal(completed.progress_percent, 100);
  assert.equal(completed.stage_status, "completed");
  assert.equal(completed.current_stage, "phase3_fabrication_audit");
  assert.equal(completed.current_stage_label, "Phase 3 · 编造审计");
  assert.equal(completed.failed_stage, null);
  assert.equal(completed.failed_agent, null);
  assert.equal(completed.stages.length, 5);
  assert.deepEqual(completed.stages.map(stage => stage.status), [
    "completed", "completed", "completed", "completed", "completed",
  ]);

  const interrupted = normalizeRunStatus({
    status: "interrupted", current_stage: "phase1_experience_diagnosis",
    failed_stage: "phase1_experience_diagnosis",
    stages: [
      { key: "step0_classification", status: "completed" },
      { key: "phase0_jd_analysis", status: "completed" },
    ],
  });
  assert.deepEqual(interrupted.stages.map(stage => stage.status), [
    "completed", "completed", "interrupted", "pending", "pending",
  ]);

  assert.equal(statusIcon("completed"), "✓");
  assert.equal(statusIcon("running"), "●");
  assert.equal(statusIcon("pending"), "○");
  assert.equal(statusIcon("failed"), "×");
  assert.equal(statusIcon("interrupted"), "×");
  console.log("frontend file merge tests passed");
})().catch(error => { console.error(error); process.exit(1); });
