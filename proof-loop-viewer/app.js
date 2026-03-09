const DEMO_PATH = "../outputs/interactive/run-20260306-163201.json";

const reloadBtn = document.getElementById("reloadBtn");
const statusChip = document.getElementById("statusChip");
const runPathText = document.getElementById("runPathText");
const problemName = document.getElementById("problemName");
const problemDesc = document.getElementById("problemDesc");
const problemVolume = document.getElementById("problemVolume");
const problemId = document.getElementById("problemId");
const finalStatus = document.getElementById("finalStatus");
const metricCards = document.getElementById("metricCards");
const timeline = document.getElementById("timeline");
const conclusionText = document.getElementById("conclusionText");
const finalProofView = document.getElementById("finalProofView");
const finalProofCode = document.getElementById("finalProofCode");
const attemptTemplate = document.getElementById("attemptTemplate");

runPathText.textContent = DEMO_PATH;

const PROBLEM_META = {
  title: "LLM이 3번 시도해 통과한 sum of cubes 공식",
  description:
    "이 run은 유명한 합 공식 하나를 두고 LLM이 Lean 오류를 받아가며 증명 코드를 수정하고, 세 번째 시도에서 최종 검증을 통과한 실제 사례입니다.",
  volumeTex:
    String.raw`4\cdot\sum_{i=0}^{n} i^3 = \bigl(n(n+1)\bigr)^2`,
  finalInductionTitle: "귀납 단계",
  finalInductionText:
    "마지막 항을 하나 추가하고, 귀납가정을 넣은 뒤, 남은 식을 단계적으로 인수분해해 목표와 같은 모양으로 맞춥니다.",
  finalInductionTex: String.raw`4\sum_{i=0}^{n+1} i^3
  = 4\left(\sum_{i=0}^{n} i^3 + (n+1)^3\right)
  = \bigl(n(n+1)\bigr)^2 + 4(n+1)^3
  = (n+1)^2n^2 + 4(n+1)^3
  = (n+1)^2\bigl(n^2 + 4(n+1)\bigr)
  = (n+1)^2(n^2 + 4n + 4)
  = (n+1)^2(n+2)^2
  = \bigl((n+1)(n+2)\bigr)^2`,
};

function safeText(v, fallback = "-") {
  if (v === null || v === undefined || v === "") return fallback;
  return String(v);
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function setStatus(text, ok = false) {
  statusChip.textContent = text;
  statusChip.style.color = ok ? "var(--ok)" : "var(--ink)";
}

function extractErrors(verifyObj) {
  if (!verifyObj || typeof verifyObj !== "object") return [];
  const out = [];
  for (const sec of ["lean_messages", "tool_messages"]) {
    const errs = verifyObj?.[sec]?.errors;
    if (!Array.isArray(errs)) continue;
    for (const e of errs) {
      const s = String(e).trim();
      if (s) out.push(s);
    }
  }
  return out;
}

function firstLine(s) {
  return String(s || "").split("\n").map((x) => x.trim()).find(Boolean) || "";
}

function shortError(msg) {
  const m = firstLine(msg)
    .replace(/\s+/g, " ")
    .replace(/^error:\s*/i, "")
    .trim();
  return m || "오류 본문 없음";
}

function explainError(msg) {
  const m = String(msg || "").toLowerCase();
  if (m.includes("could not unify")) return "지금 식과 가져온 정리가 서로 안 맞음";
  if (m.includes("unknown constant") || m.includes("unknown identifier")) return "필요한 이름이나 정리를 Lean이 찾지 못함";
  if (m.includes("rewrite") && m.includes("failed")) return "지금 식에 없는 부분을 바꾸려 함";
  if (m.includes("unsolved goals")) return "거의 맞았지만 마지막 목표를 닫지 못함";
  if (m.includes("linarith failed")) return "자동 계산만으로는 끝나지 않음";
  return "Lean 검증 실패";
}

function extractPrevFeedbackBlock(prompt) {
  if (!prompt) return "";
  const marker = "Previous AXLE feedback:";
  const idx = prompt.indexOf(marker);
  if (idx === -1) return "";
  const rest = prompt.slice(idx + marker.length).trim();
  const cut = rest.indexOf("\n\n");
  return (cut === -1 ? rest : rest.slice(0, cut)).trim();
}

function metricCard(label, value) {
  const card = document.createElement("article");
  card.className = "metric-card";
  card.innerHTML = `<p class="label">${label}</p><p class="value">${value}</p>`;
  metricCards.appendChild(card);
}

function mathBlockHtml(label, tex, emphasis = false) {
  return `
    <div class="math-block${emphasis ? " emphasis" : ""}">
      <p class="math-label">${escapeHtml(label)}</p>
      <div>\\[${tex}\\]</div>
    </div>
  `;
}

function proofStepHtml(title, text, tex) {
  return `
    <article class="proof-step accent">
      <h3>${escapeHtml(title)}</h3>
      <p>${escapeHtml(text)}</p>
      ${mathBlockHtml(title, tex, true)}
    </article>
  `;
}

async function typesetMath() {
  if (!window.MathJax?.typesetPromise) return;
  await window.MathJax.typesetPromise([document.body]);
}

function renderMetrics(result) {
  metricCards.innerHTML = "";
  const attempts = Array.isArray(result?.attempt_records) ? result.attempt_records : [];
  metricCard("총 시도", safeText(result?.attempts_used, attempts.length));
  metricCard("첫 검증", attempts[0]?.verify_ok ? "통과" : "실패");
  metricCard("최종 결과", result?.status === "solved" ? "Lean verified" : safeText(result?.status));
  metricCard(
    "repair",
    attempts.some((a) => a.repair !== undefined || a.repair_ok !== undefined) ? "사용" : "미사용"
  );
  metricCard(
    "repair 코드 수정",
    attempts.some((a) => {
      const rep = safeText(a?.repair?.content, "").trim();
      const cand = safeText(a?.candidate, "").trim();
      const repairCalled = a?.repair !== undefined || a?.repair_ok !== undefined || a?.repair_verify !== undefined;
      return repairCalled && !!rep && rep !== cand;
    })
      ? "있음"
      : "없음"
  );
}

function summarizeRepair(at) {
  const called = at?.repair !== undefined || at?.repair_ok !== undefined || at?.repair_verify !== undefined;
  if (!called) return "repair 미실행(verify 단계에서 종료)";

  const rep = safeText(at?.repair?.content, "").trim();
  const cand = safeText(at?.candidate, "").trim();
  const changed = !!rep && rep !== cand;
  const ok = at?.repair_ok === true;

  if (ok) return changed ? "repair가 코드를 수정했고 재검증 통과" : "repair 후 재검증 통과";
  return changed ? "repair가 코드를 수정했지만 재검증 실패" : "repair가 코드를 바꾸지 못했고 재검증 실패";
}

function badgeHtml(label, cls = "") {
  return `<span class="mini-badge${cls ? ` ${cls}` : ""}">${escapeHtml(label)}</span>`;
}

function llmSummary(idx) {
  if (idx === 0) return "먼저 전체 합을 `이전까지의 합 + 마지막 한 항`으로 나눠 보려 했습니다.";
  if (idx === 1) return "첫 실패 뒤에는 표기 바꾸기를 줄이고, 이전 단계 결과를 먼저 끼워 넣으려 했습니다.";
  if (idx === 2) return "마지막에는 남은 식을 목표와 같은 모양으로 직접 맞추는 방식으로 바꿨습니다.";
  return "이전 피드백을 반영해 새 증명 후보를 만들었습니다.";
}

function renderLlmPane(at, idx) {
  const details =
    idx === 0
      ? "핵심 생각은 단순합니다. `n+1` 단계 문제를 한 번에 풀지 말고, 이미 알고 싶은 `0..n`까지의 합과 새로 붙는 마지막 항 `(n+1)^3`로 쪼개 보자는 것입니다."
      : idx === 1
        ? "첫 시도가 표기 바꾸기에서 막히자, 두 번째 시도에서는 먼저 `이전 단계에서는 이 공식이 맞다`는 귀납가정을 넣고, 그 다음 남은 식만 계산으로 정리해 보려 했습니다."
        : idx === 2
          ? "세 번째 시도에서는 식을 마구 펼치기보다, `(n+1)^2` 같은 공통 부분을 묶어 가면서 최종 목표인 `((n+1)(n+2))^2` 꼴에 정확히 맞추는 방향으로 정리했습니다."
          : "이전 피드백을 바탕으로 전략을 다시 조정했습니다.";
  return `
    <p class="pane-summary">${escapeHtml(llmSummary(idx))}</p>
    <p class="pane-meta">${escapeHtml(details)}</p>
  `;
}

function renderVerifyPane(at, idx, next) {
  const verifyErrors = extractErrors(at?.verify || {});
  const err1 = verifyErrors[0] || "";
  const feedback = next ? extractPrevFeedbackBlock(next.prompt || "") : "";
  const badges = [
    badgeHtml(at?.verify_ok ? "verify 통과" : "verify 실패", at?.verify_ok ? "ok" : "fail"),
  ].join("");

  if (at?.verify_ok) {
    return `
      <div class="badge-row">${badges}</div>
      <p class="pane-summary">Lean이 이 후보 증명을 그대로 받아들였습니다.</p>
      <p class="pane-meta">귀납가설 적용과 마지막 다항식 정리가 모두 정확히 맞아 떨어졌습니다.</p>
    `;
  }

  const reason =
    idx === 0
      ? "첫 시도는 좋은 출발이었지만, 식에 실제로 보이지 않는 부분을 바꾸려 하면서 막혔습니다. 쉽게 말해 '지금 손댈 수 없는 곳을 건드린 것'입니다."
      : idx === 1
        ? "두 번째 시도는 방향은 맞았습니다. 다만 마지막 계산이 끝까지 정리되지 않아서, Lean 기준으로는 아직 목표와 같은 식이 되지 못했습니다."
        : "이 시도에서는 Lean이 목표를 아직 닫지 못했습니다.";

  const nextStepHint =
    idx === 0
      ? "그래서 다음 시도에서는 불필요한 표기 바꾸기를 빼고, 먼저 이미 알고 있는 이전 단계 결과를 넣는 쪽으로 방향을 틀었습니다."
      : idx === 1
        ? "그래서 마지막 시도에서는 식을 더 펼치기보다, 공통 부분을 묶어 목표 모양에 직접 맞추는 방식으로 바꿨습니다."
        : feedback
          ? "이 실패 정보는 다음 시도 프롬프트로 전달됐습니다."
          : "";

  return `
    <div class="badge-row">${badges}${badgeHtml(explainError(err1), "warn")}</div>
    <p class="pane-summary">${escapeHtml(reason)}</p>
    ${
      nextStepHint
        ? `<p class="pane-meta">${escapeHtml(nextStepHint)}</p>`
        : feedback
          ? `<p class="pane-meta">이 실패 정보는 다음 시도로 전달됐습니다.</p>`
          : `<p class="empty-note">다음 시도로 전달된 추가 피드백 없음</p>`
    }
  `;
}

function renderRepairPane(at) {
  const repairCalled = at?.repair !== undefined || at?.repair_ok !== undefined || at?.repair_verify !== undefined;
  if (!repairCalled) {
    return `
      <div class="badge-row">${badgeHtml("repair 미실행")}</div>
      <p class="pane-summary">verify가 이미 통과해서 repair 단계까지 갈 필요가 없었습니다.</p>
      <p class="pane-meta">이 Attempt가 최종 성공본으로 채택되었습니다.</p>
    `;
  }

  const candidate = safeText(at?.candidate, "").trim();
  const repaired = safeText(at?.repair?.content, "").trim();
  const changed = !!repaired && repaired !== candidate;
  const summary =
    changed
      ? "AXLE repair가 코드를 자동으로 손봤지만 이번 run의 핵심 수정은 아니었습니다."
      : "AXLE repair는 실행됐지만, 이번 시도에서는 코드를 자동으로 바꾸지 못했습니다.";
  const nextFix =
    !changed && at?.attempt === 1
      ? "대신 다음 LLM 시도에서는 문제를 일으킨 rewrite를 제거하고, 더 직접적인 다항식 정리로 방향을 바꿨습니다."
      : !changed && at?.attempt === 2
        ? "대신 다음 LLM 시도에서는 다항식 전개를 줄이고 `(n+1)^2`를 묶는 인수분해 방식으로 바뀌었습니다."
        : changed
          ? "자동 수정 뒤 다시 검증했지만, 결국 최종 성공은 다음 LLM 수정본에서 나왔습니다."
          : "추가 자동 수정은 없었습니다.";

  return `
    <div class="badge-row">
      ${badgeHtml("repair 실행됨")}
      ${badgeHtml(changed ? "코드 변경 있음" : "변경 없음", changed ? "warn" : "")}
      ${badgeHtml(at?.repair_ok ? "재검증 통과" : "재검증 실패", at?.repair_ok ? "ok" : "fail")}
    </div>
    <p class="pane-summary">${escapeHtml(summary)}</p>
    <p class="pane-meta">${escapeHtml(nextFix)}</p>
  `;
}

function attemptFormula(at, idx) {
  if (idx === 0) {
    return mathBlockHtml(
      "Attempt 1의 핵심 아이디어",
      String.raw`4\cdot\sum_{i=0}^{n+1} i^3
      =
      4\cdot\left(\sum_{i=0}^{n} i^3 + (n+1)^3\right)
      \qquad
      \text{이전 합 + 마지막 항}`
    );
  }

  if (idx === 1) {
    return mathBlockHtml(
      "Attempt 2의 핵심 아이디어",
      String.raw`4\cdot\left(\sum_{i=0}^{n} i^3 + (n+1)^3\right)
      =
      \bigl(n(n+1)\bigr)^2 + 4(n+1)^3
      \qquad
      \text{귀납가정 대입 후 계산}`
    );
  }

  if (idx === 2) {
    return mathBlockHtml(
      "Attempt 3의 핵심 아이디어",
      String.raw`4\cdot\sum_{i=0}^{n+1} i^3
      =
      \bigl(n(n+1)\bigr)^2 + 4(n+1)^3
      =
      \bigl((n+1)(n+2)\bigr)^2`
    );
  }

  return mathBlockHtml(
    `Attempt ${safeText(at?.attempt, idx + 1)} 수식`,
    String.raw`4\cdot\sum_{i=0}^{n} i^3 = \bigl(n(n+1)\bigr)^2`
  );
}

function renderTimeline(result) {
  timeline.innerHTML = "";
  const attempts = Array.isArray(result?.attempt_records) ? result.attempt_records : [];

  attempts.forEach((at, idx) => {
    const next = idx + 1 < attempts.length ? attempts[idx + 1] : null;
    const node = attemptTemplate.content.firstElementChild.cloneNode(true);

    const title = node.querySelector(".attempt-title");
    const state = node.querySelector(".attempt-state");
    const formula = node.querySelector(".attempt-formula");
    const llmPane = node.querySelector(".llm-pane");
    const verifyPane = node.querySelector(".verify-pane");
    const repairPane = node.querySelector(".repair-pane");

    title.textContent = `Attempt ${safeText(at.attempt, idx + 1)}`;
    state.textContent = at.verify_ok ? "verify 통과" : "verify 실패";
    state.classList.add(at.verify_ok ? "ok" : "fail");

    formula.innerHTML = attemptFormula(at, idx);
    llmPane.innerHTML = renderLlmPane(at, idx);
    verifyPane.innerHTML = renderVerifyPane(at, idx, next);
    repairPane.innerHTML = renderRepairPane(at);
    timeline.appendChild(node);
  });
}

function renderConclusion(result) {
  const status = safeText(result?.status, "unknown");
  const attempts = safeText(result?.attempts_used, "?");

  if (status === "solved") {
    conclusionText.innerHTML = `이 예시에서는 같은 명제를 두고 LLM이 Lean 오류를 받아가며 전략을 두 번 수정했고, ${attempts}번째 시도에서 최종적으로 Lean verified에 도달했습니다. 즉, 한 run 안에서 제안 -> 검증 실패 -> 피드백 -> 재시도 -> 최종 통과가 3단계에 걸쳐 실제로 일어난 사례입니다.`;
    return;
  }

  conclusionText.innerHTML = `이 run은 ${attempts}회 시도 후에도 아직 Lean verified에 도달하지 못했습니다.`;
}

function render(data) {
  const result = Array.isArray(data?.results) ? data.results[0] : null;
  if (!result) {
    throw new Error("results[0]가 없습니다.");
  }

  if (problemName) problemName.textContent = PROBLEM_META.title;
  if (problemDesc) problemDesc.textContent = PROBLEM_META.description;
  if (problemVolume) {
    problemVolume.innerHTML = mathBlockHtml("이번 데모에서 검증하려는 명제", PROBLEM_META.volumeTex);
  }
  problemId.textContent = safeText(result.problem_id);
  finalStatus.textContent = `${safeText(result.status)} (attempts: ${safeText(result.attempts_used)})`;

  renderMetrics(result);
  renderTimeline(result);
  renderConclusion(result);
  if (finalProofView) {
    finalProofView.innerHTML = `<div class="proof-grid">${proofStepHtml(
      PROBLEM_META.finalInductionTitle,
      PROBLEM_META.finalInductionText,
      PROBLEM_META.finalInductionTex
    )}</div>`;
  }
  if (finalProofCode) {
    finalProofCode.textContent = safeText(result.final_proof, "final proof 없음");
  }
}

async function loadDemo() {
  setStatus("로딩 중...");
  try {
    const demoResp = await fetch(`${DEMO_PATH}?t=${Date.now()}`, { cache: "no-store" });
    if (!demoResp.ok) throw new Error(`repair demo HTTP ${demoResp.status}`);
    const demoData = await demoResp.json();
    render(demoData);
    await typesetMath();
    setStatus("로드 완료", true);
  } catch (err) {
    setStatus("로드 실패");
    timeline.innerHTML = `<article class="attempt-card"><h3>오류</h3><p>${safeText(err?.message, String(err))}</p></article>`;
    conclusionText.textContent = "데모 파일을 읽지 못했습니다.";
  }
}

reloadBtn.addEventListener("click", () => {
  loadDemo();
});

loadDemo();
