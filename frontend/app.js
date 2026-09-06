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

if (typeof module !== "undefined") module.exports = { mergeUploadedFiles, appendText };

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

  async function loadHistory() {
    if (!apiBase) await config();
    const session = $("session").value.trim() || "web";
    const response = await fetch(`${apiBase}/api/sessions/${encodeURIComponent(session)}/runs`, { headers: headers() });
    if (!response.ok) throw new Error(await response.text());
    const runs = (await response.json()).runs || [];
    $("history").innerHTML = runs.length ? runs.map(run =>
      `<button class="history-item" data-job="${run.job_id}"><b>${run.position_category || "未分类"}</b><span>${run.status} · ${run.final_score ?? "-"}分 · ${new Date(run.created_at).toLocaleString()}</span></button>`
    ).join("") : '<p class="muted">该会话暂无历史运行。</p>';
    document.querySelectorAll(".history-item").forEach(button =>
      button.onclick = () => showRun(button.dataset.job)
    );
  }

  async function showRun(jobId) {
    const response = await fetch(`${apiBase}/api/runs/${jobId}`, { headers: headers() });
    if (!response.ok) throw new Error(await response.text());
    renderResult(await response.json());
  }

  const STAGE_LABELS = {
    step0_classification: "Step 0 \u00b7 \u9886\u57df\u5206\u7c7b",
    phase0_jd_analysis: "Phase 0 \u00b7 JD \u5206\u6790",
    phase1_experience_diagnosis: "Phase 1 \u00b7 \u7ecf\u5386\u8bca\u65ad",
    phase2_writing_iteration: "Phase 2 \u00b7 \u64b0\u5199\u8fed\u4ee3",
    phase3_fabrication_audit: "Phase 3 \u00b7 \u7f16\u9020\u5ba1\u8ba1",
  };
  let progressStartedAt = 0;
  let progressTimer = null;
  function renderProgress(job) {
    const stages = job.stages && job.stages.length ? job.stages : Object.entries(STAGE_LABELS).map(([key,label]) => ({key,label,status:"pending"}));
    $("progressStage").textContent = job.current_stage_label || "Agent \u6b63\u5728\u5de5\u4f5c\uff0c\u8bf7\u7a0d\u540e";
    $("progressMessage").textContent = job.progress_message || "Agent \u6b63\u5728\u5de5\u4f5c\uff0c\u8bf7\u7a0d\u540e";
    $("progressFill").style.width = `${Number(job.progress_percent || 0)}%`;
    $("progressIteration").textContent = job.current_iteration && job.total_iterations ? `\u7b2c ${job.current_iteration}/${job.total_iterations} \u8f6e` : "";
    $("stageList").innerHTML = stages.map(stage => {
      const icon = stage.status === "completed" ? "\u2713" : stage.status === "failed" ? "!" : stage.status === "running" ? "\u25cf" : "\u25cb";
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
      let job = await response.json();
      while (["queued", "running"].includes(job.status)) {
        await sleep(2500);
        response = await fetch(`${apiBase}/api/runs/${job.job_id}`, { headers: headers() });
        if (!response.ok) throw new Error(await response.text());
        job = await response.json();
        renderProgress(job);
        $("status").textContent = job.status.toUpperCase();
        if (job.status === "running" && job.heartbeat_at && Date.now() - new Date(job.heartbeat_at).getTime() > 5 * 60 * 1000) {
          job = {...job, status: "interrupted", stage_status: "interrupted", progress_message: "\u4efb\u52a1\u53ef\u80fd\u56e0\u670d\u52a1\u91cd\u542f\u800c\u4e2d\u65ad"};
          renderProgress(job);
          break;
        }
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
  config().catch(() => {});
}
