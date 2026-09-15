const state = {
  dashboard: null,
  search: "",
  status: "all",
  sort: "newest",
  drawerTaskId: null,
  loading: false,
};

const $ = (id) => document.getElementById(id);
const elements = {
  workspaceName: $("workspaceName"), refreshButton: $("refreshButton"),
  totalTasks: $("totalTasks"), runningTasks: $("runningTasks"), successRate: $("successRate"), totalTokens: $("totalTokens"),
  runningHint: $("runningHint"), successHint: $("successHint"), tokenCoverage: $("tokenCoverage"),
  providerSelect: $("providerSelect"), modelSelect: $("modelSelect"), activeSelection: $("activeSelection"),
  applySelectionButton: $("applySelectionButton"), taskForm: $("taskForm"), submitTaskButton: $("submitTaskButton"),
  objectiveInput: $("objectiveInput"), constraintsInput: $("constraintsInput"), taskClassSelect: $("taskClassSelect"),
  usageTotal: $("usageTotal"), inputTokens: $("inputTokens"), outputTokens: $("outputTokens"), reasoningTokens: $("reasoningTokens"),
  inputSegment: $("inputSegment"), outputSegment: $("outputSegment"), reasoningSegment: $("reasoningSegment"),
  codexTokens: $("codexTokens"), totalDuration: $("totalDuration"), averageDuration: $("averageDuration"), measuredTasks: $("measuredTasks"), updatedTime: $("updatedTime"),
  searchInput: $("searchInput"), statusFilter: $("statusFilter"), sortSelect: $("sortSelect"),
  taskTableBody: $("taskTableBody"), emptyState: $("emptyState"), taskCount: $("taskCount"),
  detailDrawer: $("detailDrawer"), drawerBackdrop: $("drawerBackdrop"), closeDrawerButton: $("closeDrawerButton"), drawerTitle: $("drawerTitle"), drawerContent: $("drawerContent"),
  toast: $("toast"),
};

const STATUS = {
  success: ["验收通过", "success"], planning: ["Codex 规划中", "active"], reviewing: ["Codex 验收中", "active"], running: ["ZCode 执行中", "active"],
  retrying: ["ZCode 重试中", "active"], preparing: ["准备中", "active"], queued: ["排队中", "active"], needs_fix: ["需要修复", "failed"],
  failed: ["未通过", "failed"], timeout: ["已超时", "failed"], aborted: ["已中止", "failed"], unknown: ["状态未知", "unknown"],
};

const STAGES = { codex_plan: "Codex 规划", zcode_execute: "ZCode 执行", codex_review: "Codex 验收", complete: "流程结束" };

const numberFormatter = new Intl.NumberFormat("zh-CN");
const compactFormatter = new Intl.NumberFormat("zh-CN", { notation: "compact", maximumFractionDigits: 1 });
let toastTimer;

function text(value, fallback = "未记录") {
  return value === null || value === undefined || value === "" ? fallback : String(value);
}

function formatNumber(value, compact = false) {
  return Number.isFinite(value) ? (compact ? compactFormatter : numberFormatter).format(value) : "未记录";
}

function formatPercent(value) {
  return Number.isFinite(value) ? `${Math.round(value * 100)}%` : "未记录";
}

function formatDuration(value) {
  if (!Number.isFinite(value)) return "未记录";
  const seconds = Math.max(0, Math.round(value / 1000));
  if (seconds < 60) return `${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} 分 ${seconds % 60} 秒`;
  return `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分`;
}

function formatDate(value, includeDate = true) {
  if (!value || Number.isNaN(Date.parse(value))) return "未记录";
  return new Intl.DateTimeFormat("zh-CN", includeDate
    ? { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }
    : { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }
  ).format(new Date(value));
}

function workspaceLabel(path) {
  if (!path) return "未连接";
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts.at(-1) || path;
}

function showToast(message, kind = "success") {
  clearTimeout(toastTimer);
  elements.toast.textContent = message;
  elements.toast.className = `toast show${kind === "error" ? " error" : ""}`;
  toastTimer = setTimeout(() => { elements.toast.className = "toast"; }, 3600);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const payload = await response.json().catch(() => ({ ok: false, error: "服务返回了无效响应" }));
  if (!response.ok || payload.ok === false) throw new Error(payload.error || `请求失败（${response.status}）`);
  return payload;
}

function aggregateTokens(tasks) {
  return tasks.reduce((sum, task) => {
    if (!task.tokens?.available) return sum;
    sum.input += Number(task.tokens.input) || 0;
    sum.output += Number(task.tokens.output) || 0;
    sum.reasoning += Number(task.tokens.reasoning) || 0;
    return sum;
  }, { input: 0, output: 0, reasoning: 0 });
}

function codexTokenTotal(task) {
  const measured = [task.codex_usage?.planner, task.codex_usage?.reviewer].filter((usage) => usage?.available);
  return measured.length ? measured.reduce((sum, usage) => sum + (Number(usage.total) || 0), 0) : null;
}

function renderSummary() {
  const { summary, tasks, generated_at: generatedAt } = state.dashboard;
  elements.totalTasks.textContent = formatNumber(summary.total_tasks);
  elements.runningTasks.textContent = formatNumber(summary.running_tasks);
  elements.successRate.textContent = formatPercent(summary.success_rate);
  elements.totalTokens.textContent = formatNumber(summary.total_tokens, true);
  elements.runningHint.textContent = summary.running_tasks
    ? `${STAGES[state.dashboard.active_job?.stage] || "流水线"}正在处理`
    : "当前队列已清空";
  elements.successHint.textContent = `${formatNumber(summary.successful_tasks)} 项已通过 Codex 验收`;
  elements.tokenCoverage.textContent = `${formatNumber(summary.measured_token_tasks)} / ${formatNumber(summary.total_tasks)} 项有记录`;
  elements.usageTotal.textContent = formatNumber(summary.total_tokens);
  elements.codexTokens.textContent = formatNumber(summary.codex_total_tokens, true);
  elements.totalDuration.textContent = formatDuration(summary.total_duration_ms);
  elements.averageDuration.textContent = formatDuration(summary.average_duration_ms);
  elements.measuredTasks.textContent = `${formatNumber(summary.measured_token_tasks)} / ${formatNumber(summary.total_tasks)}`;
  elements.updatedTime.textContent = `更新于 ${formatDate(generatedAt, false)}`;

  const tokenParts = aggregateTokens(tasks);
  elements.inputTokens.textContent = formatNumber(tokenParts.input || null, true);
  elements.outputTokens.textContent = formatNumber(tokenParts.output || null, true);
  elements.reasoningTokens.textContent = formatNumber(tokenParts.reasoning || null, true);
  const totalParts = tokenParts.input + tokenParts.output + tokenParts.reasoning;
  const widths = totalParts ? [tokenParts.input, tokenParts.output, tokenParts.reasoning].map((value) => `${value / totalParts * 100}%`) : ["0", "0", "0"];
  [elements.inputSegment.style.width, elements.outputSegment.style.width, elements.reasoningSegment.style.width] = widths;
}

function selectedProfile() {
  return state.dashboard?.profiles?.find((profile) => profile.id === elements.providerSelect.value) || null;
}

function fillModels(preferredModel = null) {
  const profile = selectedProfile();
  const previous = preferredModel || elements.modelSelect.value;
  elements.modelSelect.replaceChildren();
  if (!profile?.models?.length) {
    elements.modelSelect.add(new Option("没有可用模型", ""));
    return;
  }
  for (const model of profile.models) elements.modelSelect.add(new Option(model, model));
  const exact = profile.models.find((model) => model.toLowerCase() === String(previous).toLowerCase());
  elements.modelSelect.value = exact || profile.models[0];
}

function renderProfiles() {
  const { profiles, selection } = state.dashboard;
  const formWasEmpty = elements.providerSelect.options.length <= 1;
  const previousProvider = formWasEmpty ? selection.provider : elements.providerSelect.value;
  const previousModel = formWasEmpty ? selection.model : elements.modelSelect.value;
  elements.providerSelect.replaceChildren();
  if (!profiles.length) elements.providerSelect.add(new Option("没有可用 API", ""));
  for (const profile of profiles) elements.providerSelect.add(new Option(`${profile.name} · ${profile.id}`, profile.id));
  elements.providerSelect.value = profiles.some((item) => item.id === previousProvider) ? previousProvider : (profiles[0]?.id || "");
  fillModels(previousModel);
  elements.activeSelection.textContent = selection.provider && selection.model ? `${selection.provider} / ${selection.model}` : "未配置";
}

function statusInfo(status) {
  return STATUS[status] || [text(status, "状态未知"), "unknown"];
}

function statusMatches(task) {
  if (state.status === "all") return true;
  if (state.status === "active") return ["planning", "running", "retrying", "reviewing", "preparing", "queued"].includes(task.status);
  if (state.status === "failed") return ["needs_fix", "failed", "timeout", "aborted"].includes(task.status);
  return task.status === state.status;
}

function filteredTasks() {
  const query = state.search.trim().toLowerCase();
  const tasks = state.dashboard.tasks.filter((task) => {
    const haystack = [task.objective, task.plan?.title, task.id, task.provider, task.model, ...(task.changed_files || [])].join(" ").toLowerCase();
    return statusMatches(task) && (!query || haystack.includes(query));
  });
  return tasks.sort((left, right) => {
    if (state.sort === "tokens") return ((codexTokenTotal(right) || 0) + (right.tokens?.total || 0)) - ((codexTokenTotal(left) || 0) + (left.tokens?.total || 0));
    if (state.sort === "duration") return (right.duration_ms ?? -1) - (left.duration_ms ?? -1);
    return Date.parse(right.started_at || 0) - Date.parse(left.started_at || 0);
  });
}

function cell(content, className = "") {
  const td = document.createElement("td");
  td.className = className;
  if (content instanceof Node) td.append(content); else td.textContent = content;
  return td;
}

function renderTasks() {
  if (!state.dashboard) return;
  const tasks = filteredTasks();
  elements.taskTableBody.replaceChildren();
  elements.emptyState.hidden = tasks.length > 0;
  elements.taskCount.textContent = `显示 ${tasks.length} / ${state.dashboard.tasks.length} 项`;
  for (const task of tasks) {
    const row = document.createElement("tr");
    const title = document.createElement("div");
    const titleStrong = document.createElement("strong"); titleStrong.className = "task-title"; titleStrong.textContent = task.objective;
    const id = document.createElement("span"); id.className = "task-id"; id.textContent = task.id;
    title.append(titleStrong, id);
    row.append(cell(title));

    const [statusLabel, statusClass] = statusInfo(task.status);
    const badge = document.createElement("span"); badge.className = `status-badge status-${statusClass}`; badge.textContent = statusLabel;
    const statusCell = document.createElement("div"); statusCell.className = "status-cell"; statusCell.append(badge);
    const stage = document.createElement("small"); stage.textContent = STAGES[task.stage] || (task.plan ? "受控流水线" : "历史任务"); statusCell.append(stage);
    row.append(cell(statusCell));

    const provider = document.createElement("div"); provider.className = "api-cell";
    const providerName = document.createElement("strong"); providerName.textContent = text(task.provider);
    const modelName = document.createElement("span"); modelName.textContent = text(task.model);
    provider.append(providerName, modelName); row.append(cell(provider));

    const tokenCell = document.createElement("div"); tokenCell.className = "token-cell";
    const codexTokens = document.createElement("span"); codexTokens.textContent = `C ${formatNumber(codexTokenTotal(task), true)}`;
    const zcodeTokens = document.createElement("span"); zcodeTokens.textContent = `Z ${formatNumber(task.tokens?.available ? task.tokens.total : null, true)}`;
    tokenCell.append(codexTokens, zcodeTokens); row.append(cell(tokenCell, "numeric"));
    row.append(cell(formatDuration(task.duration_ms), Number.isFinite(task.duration_ms) ? "numeric" : "muted-value"));
    const changes = document.createElement("span"); changes.className = "change-count";
    const changeLabel = document.createElement("span"); changeLabel.textContent = "文件";
    const changeNumber = document.createElement("b"); changeNumber.textContent = task.changed_files?.length ?? 0;
    changes.append(changeNumber, changeLabel); row.append(cell(changes));
    row.append(cell(formatDate(task.started_at)));
    const button = document.createElement("button"); button.className = "detail-button"; button.type = "button"; button.dataset.taskId = task.id; button.textContent = "查看";
    row.append(cell(button));
    elements.taskTableBody.append(row);
  }
}

function detailSection(title) {
  const section = document.createElement("section"); section.className = "detail-section";
  const heading = document.createElement("h3"); heading.textContent = title; section.append(heading);
  return section;
}

function detailGrid(items) {
  const grid = document.createElement("div"); grid.className = "detail-grid";
  for (const [label, value] of items) {
    const item = document.createElement("div"); const span = document.createElement("span"); const strong = document.createElement("strong");
    span.textContent = label; strong.textContent = text(value); item.append(span, strong); grid.append(item);
  }
  return grid;
}

function appendFileList(section, files, emptyCopy = "没有记录文件") {
  const list = document.createElement("ul"); list.className = "file-list";
  for (const file of files?.length ? files : [emptyCopy]) { const item = document.createElement("li"); item.textContent = file; list.append(item); }
  section.append(list);
}

function appendTextList(section, items, emptyCopy = "没有记录") {
  const list = document.createElement("ol"); list.className = "text-list";
  for (const value of items?.length ? items : [emptyCopy]) { const item = document.createElement("li"); item.textContent = value; list.append(item); }
  section.append(list);
}

function openDrawer(taskId, shouldFocus = true) {
  const task = state.dashboard.tasks.find((item) => item.id === taskId);
  if (!task) return;
  state.drawerTaskId = taskId;
  elements.drawerTitle.textContent = statusInfo(task.status)[0];
  elements.drawerContent.replaceChildren();

  const overview = detailSection("任务目标"); const objective = document.createElement("p"); objective.className = "detail-objective"; objective.textContent = task.objective; overview.append(objective);
  overview.append(detailGrid([["当前阶段", STAGES[task.stage]], ["审核结论", task.review?.verdict], ["ZCode API", task.provider], ["ZCode 模型", task.model], ["尝试次数", task.attempt_count], ["产物质量", task.artifact_quality]]));
  elements.drawerContent.append(overview);

  if (task.plan) {
    const plan = detailSection(`Codex 实施方案 · ${text(task.plan.title)}`);
    const summary = document.createElement("p"); summary.className = "detail-copy"; summary.textContent = task.plan.summary; plan.append(summary);
    const stepsTitle = document.createElement("h4"); stepsTitle.textContent = "实施步骤"; plan.append(stepsTitle); appendTextList(plan, task.plan.steps);
    const acceptanceTitle = document.createElement("h4"); acceptanceTitle.textContent = "验收标准"; plan.append(acceptanceTitle); appendTextList(plan, task.plan.acceptance_criteria);
    const riskTitle = document.createElement("h4"); riskTitle.textContent = "风险"; plan.append(riskTitle); appendTextList(plan, task.plan.risks, "无已记录风险");
    const exclusionTitle = document.createElement("h4"); exclusionTitle.textContent = "排除项"; plan.append(exclusionTitle); appendTextList(plan, task.plan.exclusions, "无排除项");
    const validationTitle = document.createElement("h4"); validationTitle.textContent = "验证命令"; plan.append(validationTitle);
    const validationCommand = document.createElement("pre"); validationCommand.className = "validation-output"; validationCommand.textContent = task.plan.validation_command; plan.append(validationCommand);
    elements.drawerContent.append(plan);
  }

  const codexToken = detailSection("Codex Token · 规划 / 验收"); const codexTokenGrid = document.createElement("div"); codexTokenGrid.className = "token-detail";
  for (const [stage, usage] of [["规划", task.codex_usage?.planner], ["验收", task.codex_usage?.reviewer]]) {
    for (const [part, value] of [["总计", usage?.total], ["输入", usage?.input], ["输出", usage?.output], ["推理", usage?.reasoning]]) {
      const item = document.createElement("div"); const span = document.createElement("span"); const strong = document.createElement("strong"); span.textContent = `${stage} ${part}`; strong.textContent = formatNumber(usage?.available ? value : null, true); item.append(span, strong); codexTokenGrid.append(item);
    }
  }
  codexToken.append(codexTokenGrid); elements.drawerContent.append(codexToken);

  const token = detailSection("ZCode Token 构成"); const tokenGrid = document.createElement("div"); tokenGrid.className = "token-detail";
  for (const [label, value] of [["总计", task.tokens?.total], ["输入", task.tokens?.input], ["输出", task.tokens?.output], ["推理", task.tokens?.reasoning]]) {
    const item = document.createElement("div"); const span = document.createElement("span"); const strong = document.createElement("strong"); span.textContent = label; strong.textContent = formatNumber(task.tokens?.available ? value : null, true); item.append(span, strong); tokenGrid.append(item);
  }
  token.append(tokenGrid);
  if (!task.tokens?.available) { const missing = document.createElement("p"); missing.className = "error-copy"; missing.textContent = `未记录原因：${text(task.tokens?.unavailable_reason)}`; token.append(missing); }
  elements.drawerContent.append(token);

  const timeline = detailSection("时间线"); timeline.append(detailGrid([["任务开始", task.started_at ? new Date(task.started_at).toLocaleString("zh-CN") : null], ["方案完成", task.plan_completed_at ? new Date(task.plan_completed_at).toLocaleString("zh-CN") : null], ["ZCode 完成", task.worker_completed_at ? new Date(task.worker_completed_at).toLocaleString("zh-CN") : null], ["审核完成", task.review_completed_at ? new Date(task.review_completed_at).toLocaleString("zh-CN") : null], ["总耗时", formatDuration(task.duration_ms)], ["任务 ID", task.id]])); elements.drawerContent.append(timeline);
  const changed = detailSection(`变更文件 · ${task.changed_files?.length || 0}`); appendFileList(changed, task.changed_files); elements.drawerContent.append(changed);
  const allowed = detailSection(`允许范围 · ${task.allowed_files?.length || 0}`); appendFileList(allowed, task.allowed_files); elements.drawerContent.append(allowed);

  if (task.review) {
    const review = detailSection(`Codex 审核 · ${task.review.verdict}`);
    const summary = document.createElement("p"); summary.className = "detail-copy"; summary.textContent = task.review.summary; review.append(summary);
    const findings = task.review.findings?.map((item) => `${item.severity.toUpperCase()} · ${item.title} · ${item.file}${item.line ? `:${item.line}` : ""} — ${item.details}`) || [];
    appendTextList(review, findings, "未发现阻塞问题");
    const criteriaTitle = document.createElement("h4"); criteriaTitle.textContent = "验收证据"; review.append(criteriaTitle);
    appendTextList(review, task.review.criteria?.map((item) => `${item.status.toUpperCase()} · ${item.criterion} — ${item.evidence}`), "未记录验收证据");
    const fixesTitle = document.createElement("h4"); fixesTitle.textContent = "建议修复"; review.append(fixesTitle);
    appendTextList(review, task.review.recommended_fixes, "无");
    elements.drawerContent.append(review);
  }

  const audit = detailSection("主管审计");
  const evidence = document.createElement("ul"); evidence.className = "evidence-list";
  const checks = task.audit?.artifact_review_checklist || [];
  for (const check of checks.length ? checks : [{ id: "审计记录", status: task.audit?.ok === true ? "pass" : "未记录" }]) {
    const item = document.createElement("li"); item.textContent = check.id; const result = document.createElement("b"); result.textContent = text(check.status); item.append(result); evidence.append(item);
  }
  audit.append(evidence); elements.drawerContent.append(audit);

  const validation = detailSection("验证结果");
  validation.append(detailGrid([["结果", task.validation?.ok === true ? "通过" : task.validation?.ok === false ? "未通过" : null], ["退出码", task.validation?.returncode]]));
  const output = [task.validation?.stdout_tail, task.validation?.stderr_tail].filter(Boolean).join("\n");
  if (output) { const pre = document.createElement("pre"); pre.className = "validation-output"; pre.textContent = output; validation.append(pre); }
  elements.drawerContent.append(validation);
  if (task.failure_reason) { const failure = detailSection("失败原因"); const copy = document.createElement("p"); copy.className = "error-copy"; copy.textContent = task.failure_reason; failure.append(copy); elements.drawerContent.append(failure); }

  elements.drawerBackdrop.hidden = false;
  requestAnimationFrame(() => elements.detailDrawer.classList.add("open"));
  elements.detailDrawer.setAttribute("aria-hidden", "false");
  if (shouldFocus) elements.closeDrawerButton.focus();
}

function closeDrawer() {
  state.drawerTaskId = null;
  elements.detailDrawer.classList.remove("open");
  elements.detailDrawer.setAttribute("aria-hidden", "true");
  setTimeout(() => { elements.drawerBackdrop.hidden = true; }, 280);
}

async function refresh({ quiet = false } = {}) {
  if (state.loading) return;
  state.loading = true;
  elements.refreshButton.classList.add("loading");
  try {
    state.dashboard = await api("/api/dashboard");
    elements.workspaceName.textContent = workspaceLabel(state.dashboard.workspace);
    elements.workspaceName.parentElement.parentElement.title = state.dashboard.workspace;
    renderSummary(); renderProfiles(); renderTasks();
    if (state.drawerTaskId) openDrawer(state.drawerTaskId, false);
    if (!quiet) showToast("任务数据已同步");
  } catch (error) {
    if (!state.dashboard) {
      elements.taskTableBody.replaceChildren();
      const row = document.createElement("tr"); row.className = "loading-row"; const td = document.createElement("td"); td.colSpan = 8; td.textContent = `连接失败：${error.message}`; row.append(td); elements.taskTableBody.append(row);
    }
    showToast(error.message, "error");
  } finally {
    state.loading = false;
    elements.refreshButton.classList.remove("loading");
  }
}

async function applySelection() {
  const provider = elements.providerSelect.value; const model = elements.modelSelect.value;
  if (!provider || !model) return showToast("请选择 API 和模型", "error");
  elements.applySelectionButton.disabled = true;
  try {
    await api("/api/selection", { method: "POST", body: JSON.stringify({ provider, model }) });
    showToast(`已应用 ${provider} / ${model}`); await refresh({ quiet: true });
  } catch (error) { showToast(error.message, "error"); }
  finally { elements.applySelectionButton.disabled = false; }
}

async function submitTask(event) {
  event.preventDefault();
  const payload = {
    objective: elements.objectiveInput.value.trim(), provider: elements.providerSelect.value, model: elements.modelSelect.value,
    constraints: elements.constraintsInput.value.trim(), task_class: elements.taskClassSelect.value,
  };
  elements.submitTaskButton.disabled = true;
  elements.submitTaskButton.querySelector("span").textContent = "正在启动…";
  try {
    await api("/api/tasks", { method: "POST", body: JSON.stringify(payload) });
    showToast("Codex 正在分析仓库并制定方案");
    elements.objectiveInput.value = ""; elements.constraintsInput.value = "";
    await refresh({ quiet: true });
  } catch (error) { showToast(error.message, "error"); }
  finally { elements.submitTaskButton.disabled = false; elements.submitTaskButton.querySelector("span").textContent = "让 Codex 制定方案"; }
}

elements.providerSelect.addEventListener("change", () => fillModels());
elements.applySelectionButton.addEventListener("click", applySelection);
elements.taskForm.addEventListener("submit", submitTask);
elements.refreshButton.addEventListener("click", () => refresh());
elements.searchInput.addEventListener("input", (event) => { state.search = event.target.value; renderTasks(); });
elements.statusFilter.addEventListener("change", (event) => { state.status = event.target.value; renderTasks(); });
elements.sortSelect.addEventListener("change", (event) => { state.sort = event.target.value; renderTasks(); });
elements.taskTableBody.addEventListener("click", (event) => { const button = event.target.closest("[data-task-id]"); if (button) openDrawer(button.dataset.taskId); });
elements.closeDrawerButton.addEventListener("click", closeDrawer);
elements.drawerBackdrop.addEventListener("click", closeDrawer);
document.addEventListener("keydown", (event) => { if (event.key === "Escape" && state.drawerTaskId) closeDrawer(); });

refresh({ quiet: true });
setInterval(() => refresh({ quiet: true }), 5000);
