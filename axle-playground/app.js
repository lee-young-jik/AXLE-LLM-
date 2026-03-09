const els = {
  serverBanner: document.getElementById("serverBanner"),
  llmProvider: document.getElementById("llmProvider"),
  llmModel: document.getElementById("llmModel"),
  refreshModelsBtn: document.getElementById("refreshModelsBtn"),
  openrouterKeyWrap: document.getElementById("openrouterKeyWrap"),
  openrouterApiKey: document.getElementById("openrouterApiKey"),
  openrouterApproveBtn: document.getElementById("openrouterApproveBtn"),
  openrouterApproveState: document.getElementById("openrouterApproveState"),
  axleKeyWrap: document.getElementById("axleKeyWrap"),
  axleApiKey: document.getElementById("axleApiKey"),
  axleApproveBtn: document.getElementById("axleApproveBtn"),
  axleApproveState: document.getElementById("axleApproveState"),
  environment: document.getElementById("environment"),
  maxAttempts: document.getElementById("maxAttempts"),
  useMathlib: document.getElementById("useMathlib"),
  problemId: document.getElementById("problemId"),
  formalStatement: document.getElementById("formalStatement"),
  useRepair: document.getElementById("useRepair"),
  repairs: document.getElementById("repairs"),
  terminalTactics: document.getElementById("terminalTactics"),
  fallbackTactics: document.getElementById("fallbackTactics"),
  startBtn: document.getElementById("startBtn"),
  sampleBtn: document.getElementById("sampleBtn"),
  runStatus: document.getElementById("runStatus"),
  jobMeta: document.getElementById("jobMeta"),
  timeline: document.getElementById("timeline"),
  finalProof: document.getElementById("finalProof"),
  summaryStatus: document.getElementById("summaryStatus"),
  summaryAttempts: document.getElementById("summaryAttempts"),
  summarySolvedVia: document.getElementById("summarySolvedVia"),
};

let pollTimer = null;
let configState = { providers: [], default_provider: null, axle_ready: false };
let openrouterApproved = false;
let axleApproved = false;

const SAMPLE = {
  problem_id: "sum_cubes_formula_demo",
  formal_statement: `import Mathlib

theorem sum_cubes_formula_demo (n : Nat) :
    4 * (Finset.range (n + 1)).sum (fun i => i ^ 3) = (n * (n + 1)) ^ 2 := by
  sorry`,
};

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function setRunStatus(text) {
  els.runStatus.textContent = text;
}

function setBanner(text, kind = "neutral") {
  els.serverBanner.textContent = text;
  els.serverBanner.className = `banner ${kind}`;
}

function isLikelyAxleKey(key) {
  const k = String(key || "").trim();
  return k.startsWith("pk_") && k.length >= 15;
}

function isLikelyOpenRouterKey(key) {
  const k = String(key || "").trim();
  return k.startsWith("sk-or-v1-") && k.length >= 26;
}

function setModelControlsEnabled(enabled) {
  els.llmModel.disabled = !enabled;
  els.refreshModelsBtn.disabled = !enabled;
}

function setOpenrouterApproval(approved, message = "", tone = "") {
  openrouterApproved = approved;
  if (els.llmProvider.value === "openrouter") {
    setModelControlsEnabled(approved);
  }
  if (!els.openrouterApproveState) return;

  const defaultMsg = approved
    ? "승인 완료: 모델을 선택할 수 있습니다."
    : "승인 대기: 키를 입력하고 승인 버튼을 눌러주세요.";
  const nextTone = tone || (approved ? "ok" : "wait");
  els.openrouterApproveState.className = `approval-note ${nextTone}`;
  els.openrouterApproveState.textContent = message || defaultMsg;
}

function setAxleApproval(approved, message = "", tone = "") {
  axleApproved = approved;
  if (!els.axleApproveState) return;
  const defaultMsg = approved
    ? "승인 완료: AXLE 검증을 사용할 수 있습니다."
    : "승인 대기: AXLE 키를 입력하고 승인 버튼을 눌러주세요.";
  const nextTone = tone || (approved ? "ok" : "wait");
  els.axleApproveState.className = `approval-note ${nextTone}`;
  els.axleApproveState.textContent = message || defaultMsg;
}

function loadSample() {
  els.problemId.value = SAMPLE.problem_id;
  els.formalStatement.value = SAMPLE.formal_statement;
}

function providerDefaults(providerId) {
  const hit = configState.providers.find((p) => p.id === providerId);
  return hit?.default_model || "";
}

function fillModelSelect(models, preferred = "") {
  const options = Array.isArray(models) ? models : [];
  const fallback = preferred || "model";
  if (options.length === 0) {
    els.llmModel.innerHTML = `<option value="${escapeHtml(fallback)}">${escapeHtml(fallback)}</option>`;
    els.llmModel.value = fallback;
    return;
  }
  els.llmModel.innerHTML = options
    .map((m) => `<option value="${escapeHtml(m.id)}">${escapeHtml(m.name || m.id)}</option>`)
    .join("");
  const hasPreferred = options.some((m) => m.id === preferred);
  els.llmModel.value = hasPreferred ? preferred : options[0].id;
}

function refreshProviderUi() {
  const current = els.llmProvider.value;
  const isOpenRouter = current === "openrouter";
  els.openrouterKeyWrap.classList.toggle("hidden", !isOpenRouter);
  if (isOpenRouter) {
    setModelControlsEnabled(openrouterApproved);
  } else {
    setModelControlsEnabled(true);
  }
}

function fillProviders() {
  const providerOptions = configState.providers.length
    ? configState.providers
    : [
        { id: "openrouter", label: "OpenRouter", default_model: "openai/gpt-4.1-mini" },
        { id: "openai", label: "OpenAI", default_model: "gpt-4.1-mini" },
      ];

  els.llmProvider.innerHTML = providerOptions
    .map((p) => `<option value="${escapeHtml(p.id)}">${escapeHtml(p.label)}</option>`)
    .join("");

  const selected = configState.default_provider || providerOptions[0].id;
  els.llmProvider.value = selected;
  refreshProviderUi();
}

function collectPayload() {
  return {
    llm_provider: els.llmProvider.value,
    axle_api_key: els.axleApiKey.value.trim(),
    llm_api_key: els.llmProvider.value === "openrouter" ? els.openrouterApiKey.value.trim() : "",
    llm_model: els.llmModel.value,
    environment: els.environment.value.trim(),
    max_attempts: Number(els.maxAttempts.value || 6),
    use_mathlib: els.useMathlib.checked,
    problem_id: els.problemId.value.trim(),
    hint: "",
    formal_statement: els.formalStatement.value,
    use_repair: els.useRepair.checked,
    repairs: els.repairs.value,
    terminal_tactics: els.terminalTactics.value,
    fallback_tactics: els.fallbackTactics.value,
  };
}

function firstError(errors) {
  if (!Array.isArray(errors) || errors.length === 0) return "-";
  return String(errors[0]).replace(/\s+/g, " ").trim();
}

function renderAttempts(events, result) {
  const attempts = new Map();

  for (const event of events) {
    if (!["attempt_started", "candidate", "verify_result", "repair_result", "feedback_ready", "fallback_result"].includes(event.type)) {
      continue;
    }
    const attempt = Number(event.attempt || 0);
    if (!attempt) continue;
    if (!attempts.has(attempt)) {
      attempts.set(attempt, { attempt });
    }
    const slot = attempts.get(attempt);
    slot[event.type] = event;
  }

  const cards = [...attempts.values()].map((slot) => {
    const verify = slot.verify_result;
    const repair = slot.repair_result;
    const feedback = slot.feedback_ready;
    const done = result && Number(result.attempts_used) === slot.attempt && result.status === "solved" && verify?.ok;

    return `
      <article class="attempt-card">
        <div class="attempt-head">
          <h3>Attempt ${slot.attempt}</h3>
          <div class="badges">
            <span class="badge ${verify?.ok ? "ok" : "fail"}">${verify?.ok ? "verify 통과" : "verify 실패"}</span>
            ${repair ? `<span class="badge ${repair.changed ? "warn" : ""}">${repair.changed ? "repair 변경 있음" : "repair 변경 없음"}</span>` : `<span class="badge">repair 미실행</span>`}
            ${done ? `<span class="badge ok">최종 채택</span>` : ""}
          </div>
        </div>
        <div class="attempt-grid">
          <section class="event-card">
            <h4>LLM Prompt</h4>
            <pre>${escapeHtml(slot.attempt_started?.prompt || "-")}</pre>
          </section>
          <section class="event-card">
            <h4>LLM Candidate</h4>
            <pre>${escapeHtml(slot.candidate?.candidate || "-")}</pre>
          </section>
          <section class="event-card">
            <h4>Verify / Repair</h4>
            <p><strong>verify:</strong> ${verify?.ok ? "통과" : "실패"}</p>
            <p><strong>첫 오류:</strong> ${escapeHtml(firstError(verify?.errors))}</p>
            <p><strong>repair:</strong> ${repair ? (repair.ok ? "재검증 통과" : repair.changed ? "수정했지만 실패" : "실행했지만 변경 없음") : "실행 안 함"}</p>
            ${repair?.first_change ? `<p><strong>첫 변경:</strong> ${escapeHtml(repair.first_change)}</p>` : ""}
            ${feedback?.feedback ? `<p><strong>다음 시도 피드백:</strong> ${escapeHtml(feedback.feedback)}</p>` : ""}
          </section>
        </div>
      </article>
    `;
  });

  if (cards.length === 0) {
    els.timeline.className = "timeline empty-state";
    els.timeline.textContent = "아직 attempt 이벤트가 없습니다.";
    return;
  }
  els.timeline.className = "timeline";
  els.timeline.innerHTML = cards.join("");
}

function renderJob(job) {
  els.jobMeta.textContent = `job ${job.job_id} · ${job.updated_at}`;
  els.summaryStatus.textContent = job.status;

  const result = job.result || null;
  els.summaryAttempts.textContent = result ? String(result.attempts_used || "-") : "-";
  els.summarySolvedVia.textContent = result?.solved_via || (result?.status === "solved" ? "LLM / repair" : "-");
  els.finalProof.textContent = result?.final_proof || "-";

  renderAttempts(job.events || [], result);

  if (["solved", "failed", "error"].includes(job.status)) {
    setRunStatus(job.status);
  } else {
    setRunStatus("실행 중");
  }
}

async function pollJob(jobId) {
  try {
    const resp = await fetch(`/api/runs/${jobId}`);
    const data = await resp.json();
    if (!resp.ok) {
      throw new Error(data.error || `HTTP ${resp.status}`);
    }
    renderJob(data);
    if (["solved", "failed", "error"].includes(data.status)) {
      window.clearInterval(pollTimer);
      pollTimer = null;
      els.startBtn.disabled = false;
    }
  } catch (err) {
    setRunStatus(`오류: ${err.message}`);
    window.clearInterval(pollTimer);
    pollTimer = null;
    els.startBtn.disabled = false;
  }
}

async function startRun() {
  if (!axleApproved) {
    setRunStatus("먼저 AXLE 키를 승인해 주세요.");
    return;
  }
  if (els.llmProvider.value === "openrouter" && !openrouterApproved) {
    setRunStatus("먼저 OpenRouter 키를 승인해 주세요.");
    return;
  }
  const payload = collectPayload();
  els.startBtn.disabled = true;
  setRunStatus("실행 중");
  els.timeline.className = "timeline empty-state";
  els.timeline.textContent = "job 생성 중...";
  els.finalProof.textContent = "-";

  try {
    const resp = await fetch("/api/prove", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (resp.status === 404) {
      await startRunLegacy(payload);
      return;
    }
    const data = await resp.json();
    if (!resp.ok) {
      throw new Error(data.error || `HTTP ${resp.status}`);
    }
    renderJob(data);
    setRunStatus(data.status || "done");
    els.startBtn.disabled = false;
  } catch (err) {
    els.startBtn.disabled = false;
    setRunStatus(`오류: ${err.message}`);
    els.timeline.textContent = err.message;
  }
}

async function startRunLegacy(payload) {
  const resp = await fetch("/api/runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await resp.json();
  if (!resp.ok) {
    throw new Error(data.error || `HTTP ${resp.status}`);
  }
  setRunStatus("queued");
  els.jobMeta.textContent = `job ${data.job_id}`;
  if (pollTimer) window.clearInterval(pollTimer);
  await pollJob(data.job_id);
  pollTimer = window.setInterval(() => pollJob(data.job_id), 1200);
}

async function loadConfig() {
  try {
    const resp = await fetch("/api/config");
    const data = await resp.json();
    if (!resp.ok) {
      throw new Error(data.error || `HTTP ${resp.status}`);
    }
    configState = data;
    fillProviders();
    setAxleApproval(false);
    if (els.llmProvider.value === "openrouter") {
      setOpenrouterApproval(false);
      fillModelSelect([], providerDefaults("openrouter"));
    } else {
      await loadModels({ silent: true });
    }
    if (!data.providers || data.providers.length === 0) {
      setBanner("사용 가능한 provider 설정을 불러오지 못했습니다.", "fail");
      els.startBtn.disabled = true;
      return;
    }
    const labels = data.providers
      .map((p) => {
        if (p.id === "openrouter") {
          return `${p.label}(${p.default_model}, key: 직접입력 필수)`;
        }
        return `${p.label}(${p.default_model})`;
      })
      .join(", ");
    setBanner(`서버 준비 완료. 사용 가능한 provider: ${labels}. AXLE key도 직접 입력/승인 필요`, "ok");
  } catch (err) {
    setBanner(`설정 로드 실패: ${err.message}`, "fail");
    els.startBtn.disabled = true;
  }
}

async function loadModels({ silent = false, bypassApproval = false } = {}) {
  const provider = els.llmProvider.value;
  if (provider === "openrouter" && !openrouterApproved && !bypassApproval) {
    fillModelSelect([], providerDefaults("openrouter"));
    if (!silent) {
      setRunStatus("OpenRouter는 키 승인 후 모델을 불러올 수 있습니다.");
    }
    return false;
  }
  const key = provider === "openrouter" ? els.openrouterApiKey.value.trim() : "";
  const preferred = providerDefaults(provider);
  const prevSelected = els.llmModel.value;

  try {
    const resp = await fetch("/api/models", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ llm_provider: provider, llm_api_key: key }),
    });
    const data = await resp.json();
    if (!resp.ok) {
      throw new Error(data.error || `HTTP ${resp.status}`);
    }
    fillModelSelect(data.models || [], prevSelected || preferred);
    if (!silent) {
      setRunStatus(`모델 ${data.count ?? 0}개 로드`);
    }
    return true;
  } catch (err) {
    fillModelSelect([], prevSelected || preferred);
    if (!silent) {
      setRunStatus(`모델 로드 실패: ${err.message}`);
    }
    return false;
  }
}

els.llmProvider.addEventListener("change", async () => {
  refreshProviderUi();
  if (els.llmProvider.value === "openrouter") {
    setOpenrouterApproval(false, "승인 대기: 키를 입력하고 승인 버튼을 눌러주세요.", "wait");
    fillModelSelect([], providerDefaults("openrouter"));
    return;
  }
  await loadModels();
});
els.openrouterApiKey.addEventListener("input", async () => {
  if (els.llmProvider.value === "openrouter") {
    setOpenrouterApproval(false, "키가 변경되었습니다. 다시 승인해 주세요.", "wait");
    fillModelSelect([], providerDefaults("openrouter"));
  }
});
els.axleApiKey.addEventListener("input", async () => {
  setAxleApproval(false, "키가 변경되었습니다. 다시 승인해 주세요.", "wait");
});
els.refreshModelsBtn.addEventListener("click", async () => {
  await loadModels();
});
els.openrouterApproveBtn.addEventListener("click", async () => {
  if (els.llmProvider.value !== "openrouter") {
    return;
  }
  const key = els.openrouterApiKey.value.trim();
  if (!key) {
    setOpenrouterApproval(false, "OpenRouter 키를 입력해 주세요.", "fail");
    setRunStatus("OpenRouter 키가 필요합니다.");
    return;
  }
  if (!isLikelyOpenRouterKey(key)) {
    setOpenrouterApproval(false, "키 형식이 올바르지 않습니다. (sk-or-v1-...)", "fail");
    setRunStatus("OpenRouter 키 형식을 확인해 주세요.");
    return;
  }
  setOpenrouterApproval(false, "승인 중... 모델 목록을 확인하고 있습니다.", "wait");
  const ok = await loadModels({ silent: true, bypassApproval: true });
  if (ok) {
    setOpenrouterApproval(true);
    setRunStatus("OpenRouter 키 승인 완료");
  } else {
    setOpenrouterApproval(false, "승인 실패: 키를 확인하고 다시 시도하세요.", "fail");
  }
});
els.axleApproveBtn.addEventListener("click", async () => {
  const key = els.axleApiKey.value.trim();
  if (!key) {
    setAxleApproval(false, "AXLE 키를 입력해 주세요.", "fail");
    setRunStatus("AXLE 키가 필요합니다.");
    return;
  }
  if (!isLikelyAxleKey(key)) {
    setAxleApproval(false, "키 형식이 올바르지 않습니다. (pk_...)", "fail");
    setRunStatus("AXLE 키 형식을 확인해 주세요.");
    return;
  }
  setAxleApproval(false, "승인 중... AXLE에 확인 요청 중입니다.", "wait");
  try {
    const resp = await fetch("/api/axle-check", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        axle_api_key: key,
        environment: els.environment.value.trim() || "lean-4.28.0",
      }),
    });
    const data = await resp.json();
    if (!resp.ok || !data.ok) {
      throw new Error(data.error || `HTTP ${resp.status}`);
    }
    setAxleApproval(true);
    setRunStatus("AXLE 키 승인 완료");
  } catch (err) {
    setAxleApproval(false, `승인 실패: ${err.message}`, "fail");
    setRunStatus(`AXLE 키 승인 실패: ${err.message}`);
  }
});
els.sampleBtn.addEventListener("click", loadSample);
els.startBtn.addEventListener("click", startRun);

loadSample();
loadConfig();
