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
    const response = await fetch("/api/config");
    apiBase = (await response.json()).apiBaseUrl.replace(/\/$/, "");
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

  function renderResult(job) {
    $("resultPanel").classList.remove("hidden");
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
        $("status").textContent = job.status.toUpperCase();
      }
      renderResult(job);
      await loadHistory();
    } catch (error) {
      $("status").textContent = "FAILED";
      $("result").textContent = error.message;
    } finally {
      $("run").disabled = false;
    }
  };
  config().catch(() => {});
}
