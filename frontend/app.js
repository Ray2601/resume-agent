const REFERENCE_MARKER = "===== REFERENCE RESUME:";

async function mergeUploadedFiles(files, marker = "FILE") {
  const accepted = Array.from(files || []).filter(file =>
    /\.(txt|md)$/i.test(file.name || "")
  );
  const parts = [];
  for (const file of accepted) {
    const body = (await file.text()).trim();
    if (body) parts.push(`===== ${marker}: ${file.name} =====\n${body}`);
  }
  return parts.join("\n\n");
}
function appendText(current, incoming) {
  return [current.trim(), incoming.trim()].filter(Boolean).join("\n\n");
}

const CANONICAL_STAGES = [
  ["step0_classification", "Step 0 · 领域分类"],
  ["phase0_jd_analysis", "Phase 0 · JD 分析"],
  ["phase1_experience_diagnosis", "Phase 1 · 经历诊断"],
  ["phase2_writing_iteration", "Phase 2 · 撰写迭代"],
  ["phase3_fabrication_audit", "Phase 3 · 编造审计"],
];

function normalizeRunStatus(run = {}) {
  const normalized = { ...run, status: run.status || "pending" };
  const sourceStages = new Map((Array.isArray(normalized.stages) ? normalized.stages : [])
    .map(stage => [stage.key, stage]));
  if (normalized.status === "completed") {
    normalized.progress_percent = 100;
    normalized.stage_status = "completed";
    normalized.progress_message = normalized.progress_message || "任务已完成";
    normalized.current_stage = "phase3_fabrication_audit";
    normalized.current_stage_label = "Phase 3 · 编造审计";
    normalized.failed_stage = null;
    normalized.failed_agent = null;
    normalized.stages = CANONICAL_STAGES.map(([key, label]) => ({
      ...(sourceStages.get(key) || {}), key, label, status: "completed",
    }));
  } else if (normalized.status === "failed") {
    normalized.stage_status = "failed";
  } else if (normalized.status === "interrupted") {
    normalized.stage_status = "interrupted";
  }
  if (normalized.status === "failed" || normalized.status === "interrupted") {
    const failedKey = normalized.failed_stage || normalized.current_stage;
    const failedIndex = CANONICAL_STAGES.findIndex(([key]) => key === failedKey);
    normalized.stages = CANONICAL_STAGES.map(([key, label], index) => ({
      ...(sourceStages.get(key) || {}), key, label,
      status: sourceStages.get(key)?.status === "completed" || index < failedIndex
        ? "completed" : index === failedIndex ? normalized.status : "pending",
    }));
  }
  return normalized;
}

function statusIcon(status) {
  return status === "completed" ? "✓" : status === "running" ? "●" : status === "pending" ? "○" : "×";
}

if (typeof module !== "undefined") module.exports = { mergeUploadedFiles, appendText, normalizeRunStatus, statusIcon };

if (typeof document !== "undefined") {
  let apiBase = "";
  const $ = id => document.getElementById(id);
  const headers = () => ({
    "Content-Type": "application/json",
    "X-App-Token": $("token").value,
  });
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

  async function config() {
    try {
      const response = await fetch("/api/config");
      if (!response.ok) throw new Error("Vercel config endpoint unavailable");
      apiBase = (await response.json()).apiBaseUrl.replace(/\/$/, "");
    } catch (error) {
      if (!["127.0.0.1", "localhost"].includes(location.hostname)) throw error;
      apiBase = "http://127.0.0.1:8000";
    }
  }

  function bindUpload(inputId, targetId, marker, multiple = false) {
    $(inputId).addEventListener("change", async event => {
      const text = await mergeUploadedFiles(event.target.files, marker);
      if (!text) return alert("仅支持非空的 .txt 或 .md 文件");
      $(targetId).value = multiple ? appendText($(targetId).value, text) : text.replace(/^===== .* =====\n/, "");
    });
  }

  bindUpload("jdFile", "jd", "JD");
  bindUpload("experienceFile", "experience", "EXPERIENCE");
  bindUpload("relatedFile", "related", "RELATED CONTENT", true);
  bindUpload("referenceFiles", "references", "REFERENCE RESUME", true);

  function metrics(values = {}) {
    return [["Total", values.total], ["Match", values.match], ["Data", values.data],
      ["Impact", values.impact], ["Concise", values.conciseness]]
      .map(([key, value]) => `<div class="metric"><span>${key}</span><strong>${value ?? "-"}</strong></div>`).join("");
  }

  function traceRows(rows = []) {
    return rows.map(trace => `<tr><td>${trace.step_name || "-"}</td><td>${trace.agent_name || "-"}</td><td>${trace.iteration || "-"}</td><td>${trace.score ?? "-"}</td><td>${((trace.latency_ms || 0) / 1000).toFixed(1)}s</td><td>${trace.status || "-"}</td></tr>`).join("");
  }

  async function loadHistory(restoreLatest = false) {
    if (!apiBase) await config();
    const session = $("session").value.trim() || "web";
    const response = await fetch(`${apiBase}/api/sessions/${encodeURIComponent(session)}/runs`, { headers: headers() });
    if (!response.ok) throw new Error(await response.text());
    const runs = ((await response.json()).runs || []).map(normalizeRunStatus);
    $("history").innerHTML = runs.length ? runs.map(run =>
      `<button class="history-item" data-job="${run.job_id}"><b>${run.position_category || "未分类"}</b><span>${run.status} · ${run.final_score ?? "-"}分 · ${new Date(run.created_at).toLocaleString()}</span></button>`
    ).join("") : '<p class="muted">该会话暂无历史运行。</p>';
    document.querySelectorAll(".history-item").forEach(button =>
      button.onclick = () => showRun(button.dataset.job)
    );
    if (restoreLatest && runs[0]) await showRun(runs[0].job_id);
  }

  async function showRun(jobId) {
    const response = await fetch(`${apiBase}/api/runs/${jobId}`, { headers: headers() });
    if (!response.ok) throw new Error(await response.text());
    renderResult(normalizeRunStatus(await response.json()));
  }

  const STAGE_LABELS = Object.fromEntries(CANONICAL_STAGES);
  let progressStartedAt = 0;
  let progressTimer = null;
  function renderProgress(job) {
    job = normalizeRunStatus(job);
    const stages = job.stages && job.stages.length ? job.stages : Object.entries(STAGE_LABELS).map(([key,label]) => ({key,label,status:"pending"}));
    $("progressStage").textContent = job.current_stage_label || "Agent \u6b63\u5728\u5de5\u4f5c\uff0c\u8bf7\u7a0d\u540e";
    $("progressMessage").textContent = job.progress_message || "Agent \u6b63\u5728\u5de5\u4f5c\uff0c\u8bf7\u7a0d\u540e";
    $("progressFill").style.width = `${Number(job.progress_percent || 0)}%`;
    $("progressIteration").textContent = job.current_iteration && job.total_iterations ? `\u7b2c ${job.current_iteration}/${job.total_iterations} \u8f6e` : "";
    $("stageList").innerHTML = stages.map(stage => {
      const icon = statusIcon(stage.status);
      return `<div class="stage ${stage.status || "pending"}"><span class="stage-icon">${icon}</span>${stage.label || STAGE_LABELS[stage.key] || stage.key}</div>`;
    }).join("");
    if (job.status === "failed" || job.stage_status === "failed") $("backgroundHint").textContent = "\u4efb\u52a1\u5931\u8d25\uff0c\u8bf7\u91cd\u65b0\u63d0\u4ea4";
    else if (job.status === "interrupted" || job.stage_status === "interrupted") $("backgroundHint").textContent = "\u4efb\u52a1\u53ef\u80fd\u56e0\u670d\u52a1\u91cd\u542f\u800c\u4e2d\u65ad\uff0c\u8bf7\u91cd\u65b0\u63d0\u4ea4";
    else $("backgroundHint").textContent = job.status === "completed" ? "\u4efb\u52a1\u5df2\u5b8c\u6210" : "\u4efb\u52a1\u53ef\u5728\u540e\u53f0\u7ee7\u7eed\uff0c\u8bf7\u52ff\u91cd\u590d\u63d0\u4ea4";
  }
  function startElapsed() {
    progressStartedAt = Date.now();
    clearInterval(progressTimer);
    progressTimer = setInterval(() => { $("elapsed").textContent = `${Math.floor((Date.now() - progressStartedAt) / 1000)}s`; }, 1000);
  }
  function stopElapsed() { clearInterval(progressTimer); progressTimer = null; }

  function renderResult(job) {
    job = normalizeRunStatus(job);
    $("resultPanel").classList.remove("hidden");
    renderProgress(job);
    $("status").textContent = job.status === "completed" ? `PASS · ${job.final_score}/100` : job.status.toUpperCase();
    $("metrics").innerHTML = metrics(job.eval_metrics);
    $("result").textContent = job.final_result || job.error || "等待结果…";
    $("audit").textContent = typeof job.fabrication_report === "string" ? job.fabrication_report : JSON.stringify(job.fabrication_report || {}, null, 2);
    $("traces").innerHTML = traceRows(job.traces);
    $("resultPanel").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  $("historyButton").onclick = () => loadHistory().catch(error => alert(error.message));
  $("run").onclick = async () => {
    const payload = {
      jd: $("jd").value.trim(),
      original_experience: $("experience").value.trim(),
      position_category: $("category").value,
      session_id: $("session").value.trim() || "web",
      max_iterations: Number($("iterations").value),
      fabrication_tolerance: Number($("tolerance").value),
      human_rules: $("rules").value.trim(),
      related_content: $("related").value.trim(),
      reference_resumes: $("references").value.trim(),
      anchor_content: $("anchor").value.trim(),
    };
    if (payload.jd.length < 20 || payload.original_experience.length < 20) {
      return alert("JD 和已有经历至少填写 20 个字符");
    }
    $("run").disabled = true;
    $("resultPanel").classList.remove("hidden");
    $("status").textContent = "QUEUED";
    startElapsed();
    renderProgress({status: "queued"});
    $("result").textContent = "Agent 正在工作，请稍候…";
    try {
      if (!apiBase) await config();
      let response = await fetch(`${apiBase}/api/runs`, {
        method: "POST", headers: headers(), body: JSON.stringify(payload),
      });
      if (!response.ok) throw new Error(await response.text());
      let job = normalizeRunStatus(await response.json());
      while (["queued", "running"].includes(job.status)) {
        await sleep(2500);
        response = await fetch(`${apiBase}/api/runs/${job.job_id}`, { headers: headers() });
        if (!response.ok) throw new Error(await response.text());
        job = normalizeRunStatus(await response.json());
        renderProgress(job);
        $("status").textContent = job.status.toUpperCase();
      }
      stopElapsed();
      renderResult(job);
      await loadHistory();
    } catch (error) {
      stopElapsed();
      $("status").textContent = "FAILED";
      renderProgress({status: "failed", stage_status: "failed", progress_message: error.message});
      $("result").textContent = error.message;
    } finally {
      $("run").disabled = false;
    }
  };
  config().then(() => loadHistory(true)).catch(() => {});
}
