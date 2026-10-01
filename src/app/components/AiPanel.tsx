import { getCurrentUserId } from "../lib/api";
import { useRef, useState, useEffect, useMemo } from "react";
import type { ReactNode } from "react";
import {
  Send,
  Sparkles,
  ChevronDown,
  CornerDownLeft,
  Wand2,
  ListChecks,
  SlidersHorizontal,
  PlayCircle,
  Trash2,
  FolderOpen,
} from "lucide-react";
import type { Page } from "../App";
import { api } from "../lib/api";
import { useWorkspace } from "../context/WorkspaceContext";
import { persistChatHistory, waitForChatHistory } from "../lib/chatHistoryPersistence";

interface Message {
  role: "user" | "ai";
  content: string;
  time: string;
  actions?: any[];
  meta?: Record<string, any>;
}

type AgentMode = "assisted" | "autopilot";

const contextInfo: Record<Page, { label: string; hint: string }> = {
  home: {
    label: "工作台",
    hint: "可问网站用法、研究问题和统计概念，也可进入项目继续分析",
  },
  upload: {
    label: "数据工作区",
    hint: "可检查数据质量、修正字段类型，在当前页打开模型选择，并配置模型需要的变量角色",
  },
  variables: {
    label: "数据工作区",
    hint: "可按所选模型的 requirement 配置因变量、自变量、分组变量等角色",
  },
  results: { label: "分析结果", hint: "可解释结果、预测表现、VIF 和模型对比" },
  charts: {
    label: "图表分析",
    hint: "可基于分析结果生成变量重要性、混淆矩阵和模型对比图",
  },
  explain: { label: "AI 解释", hint: "可生成或修改论文式结果表达" },
  report: { label: "图表与报告", hint: "可生成报告结构、图表说明和写作建议" },
};

const quickPrompts: Record<Page, string[]> = {
  home: ["如何创建一个项目？", "这里有哪些项目？", "我想研究收入与教育的关系，该怎么开始？"],
  upload: [
    "理解这份数据",
    "推荐适合这份数据的模型",
    "哪些变量类型可能识别错了？",
  ],
  variables: ["推荐变量设置", "帮我判断变量类型", "中介变量和调节变量怎么选？"],
  results: ["解释当前分析结果", "模型预测表现怎么看？", "VIF 结果怎么解读？"],
  charts: ["推荐结果图表", "生成变量重要性图", "混淆矩阵怎么看？"],
  explain: [
    "帮我改成论文结果段落",
    "这个结果能说明因果关系吗？",
    "生成更保守的解释",
  ],
  report: ["生成报告草稿结构", "帮我写图表说明", "结果表应该怎么汇报？"],
};

function now() {
  return new Date().toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function safeStorageKey(userId: string, projectId: string | null | undefined) {
  return `methodpal_agent_messages_v061:${userId || ""}:${projectId || "no_project"}`;
}

function initialMessage(
  page: Page,
  projectName?: string | null,
  hasDataset?: boolean,
): Message {
  const ctx = contextInfo[page] || {
    label: "当前页面",
    hint: "我会根据当前项目状态提供帮助",
  };
  const projectText = projectName
    ? `当前项目是「${projectName}」。`
    : "当前还没有选择项目。";
  const dataText = hasDataset
    ? "我已经能读取当前项目的数据摘要、变量类型、变量选择和分析结果。"
    : "当前项目还没有数据，推荐先去数据工作区上传 CSV/XLSX。";

  return {
    role: "ai",
    content: `您好！我是 MethodPal AI 方法搭子。${projectText}${dataText}\n\n当前页面：${ctx.label}。${ctx.hint}`,
    time: now(),
    actions: hasDataset
      ? []
      : [
          {
            type: "navigate",
            id: "go_upload",
            label: "去上传数据",
            payload: { page: "upload" },
          },
        ],
    meta: { kind: "initial" },
  };
}

function progressLabel(progress?: number | null) {
  const p = typeof progress === "number" ? progress : 0;
  if (p >= 85) return "已有分析结果";
  if (p >= 70) return "模型与变量已配置";
  if (p >= 55) return "已选择模型";
  if (p >= 35) return "已有数据工作区";
  if (p >= 15) return "已创建";
  return "未开始";
}

function entryPageForProject(project: any | null | undefined): Page {
  if (!project?.dataset_id) return "upload";
  if (!project?.selected_model) return "upload";
  if (!project?.analysis_id) return "upload";
  return "results";
}

function buildLobbyMessage(
  projects: any[] = [],
  activeProject?: any | null,
): Message {
  const projectLines = projects.length
    ? projects
        .slice(0, 6)
        .map(
          (p, idx) =>
            `${idx + 1}. ${p.name || p.project_id}：${progressLabel(p.progress)}${p.research_goal ? `，目标：${p.research_goal}` : ""}`,
        )
        .join("\n")
    : "当前还没有项目。";

  const actions: any[] = [];
  if (activeProject) {
    actions.push({
      type: "select_project",
      id: `enter_active_${activeProject.project_id}`,
      label: `进入当前项目：${activeProject.name}`,
      payload: { project_id: activeProject.project_id },
    });
  }
  projects.slice(0, 3).forEach((p) => {
    if (!activeProject || p.project_id !== activeProject.project_id) {
      actions.push({
        type: "select_project",
        id: `enter_${p.project_id}`,
        label: `进入项目：${p.name}`,
        payload: { project_id: p.project_id },
      });
    }
  });

  return {
    role: "ai",
    content:
      `您好！我是 MethodPal 研究助手。\n\n` +
      `可以问我如何使用网站、统计概念，或一起梳理研究需求。我会结合网站帮助和你的项目状态回答。进入具体项目后，可确认建议并继续分析；普通聊天不会直接修改数据或运行模型。\n\n` +
      `**当前项目列表：**\n${projectLines}`,
    time: now(),
    actions,
    meta: { kind: "lobby_initial" },
  };
}

function normalizeMessages(value: any): Message[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter(
      (m) =>
        m &&
        (m.role === "user" || m.role === "ai") &&
        typeof m.content === "string",
    )
    .map((m) => ({
      role: m.role,
      content: m.content,
      time: m.time || now(),
      actions: Array.isArray(m.actions) ? m.actions : undefined,
      meta: m.meta || undefined,
    }));
}

function buildDatasetSummaryMessage(dataset: any): Message {
  const profile = dataset?.profile;
  const columns = profile?.columns || [];
  const roleCandidates = columns
    .filter((c: any) => c.role_suggestion && c.role_suggestion !== "ignore")
    .slice(0, 8)
    .map((c: any) => `- ${c.name}：${c.type}，建议角色 ${c.role_suggestion}`)
    .join("\n");

  const qualityText = profile?.issue_count
    ? `检测到 ${profile.missing_count || 0} 处缺失值、${profile.outlier_count || 0} 处异常值。`
    : "暂未发现明显缺失值或异常值。";

  return {
    role: "ai",
    content:
      `### 数据摘要已读取\n\n` +
      `数据集：**${dataset.name}**\n\n` +
      `规模：${profile?.row_count ?? dataset.rows?.length ?? "—"} 行 × ${profile?.column_count ?? dataset.headers?.length ?? "—"} 列。${qualityText}\n\n` +
      `新的 workflow 会先完成数据检查，再选择模型，随后只配置该模型需要的变量角色，最后执行分析。\n\n` +
      (roleCandidates ? `**初步变量识别：**\n${roleCandidates}` : ""),
    time: now(),
    actions: [
      {
        type: "agent",
        id: "recommend_methods",
        label: "推荐分析模型",
        payload: { action: "recommend_methods" },
      },
      {
        type: "open_model_selector",
        id: "go_models",
        label: "打开模型选择",
        payload: {},
      },
      {
        type: "navigate",
        id: "go_workspace",
        label: "查看数据工作区",
        payload: { page: "upload" },
      },
      {
        type: "agent",
        id: "autopilot",
        label: "自动完成基础分析",
        payload: { action: "autopilot", mode: "autopilot" },
      },
    ],
    meta: { kind: "dataset_summary", dataset_id: dataset.dataset_id },
  };
}

function variableOptionsFromDataset(dataset: any, selectedVariables: any) {
  const columns = dataset?.profile?.columns || [];
  const manualTypes = selectedVariables?.variable_types || {};
  return columns.map((c: any) => ({
    name: c.name,
    detected_type: c.type,
    current_type: manualTypes[c.name] || c.type,
    role_suggestion: c.role_suggestion || null,
    missing_rate: c.missing_rate,
    unique_count: c.unique_count,
    top_values: c.top_values || [],
  }));
}

function datasetSummaryForAgent(dataset: any) {
  if (!dataset) return null;
  return {
    dataset_id: dataset.dataset_id,
    name: dataset.name,
    row_count: dataset.profile?.row_count,
    column_count: dataset.profile?.column_count,
    issue_count: dataset.profile?.issue_count,
    missing_count: dataset.profile?.missing_count,
    outlier_count: dataset.profile?.outlier_count,
    summary: dataset.profile?.summary,
    columns: dataset.profile?.columns || [],
  };
}

function renderInlineMarkdown(text: string) {
  const parts: Array<{
    text: string;
    kind: "plain" | "bold" | "italic" | "code";
  }> = [];
  const regex = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*]+\*)/g;
  let last = 0;
  let match: RegExpExecArray | null;
  while ((match = regex.exec(text))) {
    if (match.index > last)
      parts.push({ text: text.slice(last, match.index), kind: "plain" });
    const raw = match[0];
    if (raw.startsWith("**"))
      parts.push({ text: raw.slice(2, -2), kind: "bold" });
    else if (raw.startsWith("`"))
      parts.push({ text: raw.slice(1, -1), kind: "code" });
    else parts.push({ text: raw.slice(1, -1), kind: "italic" });
    last = match.index + raw.length;
  }
  if (last < text.length) parts.push({ text: text.slice(last), kind: "plain" });

  return parts.map((p, i) => {
    if (p.kind === "bold")
      return (
        <strong key={i} className="font-semibold text-foreground">
          {p.text}
        </strong>
      );
    if (p.kind === "italic") return <em key={i}>{p.text}</em>;
    if (p.kind === "code")
      return (
        <code key={i} className="rounded bg-background px-1 py-0.5 text-[11px]">
          {p.text}
        </code>
      );
    return <span key={i}>{p.text}</span>;
  });
}

function MarkdownMessage({ content }: { content: string }) {
  const lines = content.replace(/\r\n/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let listItems: string[] = [];

  function flushList(keyBase: string) {
    if (!listItems.length) return;
    blocks.push(
      <ul
        key={`ul-${keyBase}-${blocks.length}`}
        className="my-1.5 list-disc pl-4 space-y-0.5"
      >
        {listItems.map((item, i) => (
          <li key={i}>{renderInlineMarkdown(item)}</li>
        ))}
      </ul>,
    );
    listItems = [];
  }

  lines.forEach((line, idx) => {
    const trimmed = line.trim();
    if (!trimmed) {
      flushList(String(idx));
      blocks.push(<div key={`space-${idx}`} className="h-1" />);
      return;
    }

    const bullet = trimmed.match(/^[-*]\s+(.+)$/);
    if (bullet) {
      listItems.push(bullet[1]);
      return;
    }

    flushList(String(idx));

    const heading = trimmed.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      const level = heading[1].length;
      blocks.push(
        <div
          key={`h-${idx}`}
          className={
            level <= 2
              ? "mt-1 mb-1 font-semibold text-sm"
              : "mt-1 mb-0.5 font-semibold"
          }
        >
          {renderInlineMarkdown(heading[2])}
        </div>,
      );
      return;
    }

    blocks.push(
      <p key={`p-${idx}`} className="my-0.5">
        {renderInlineMarkdown(trimmed)}
      </p>,
    );
  });

  flushList("end");
  return <div className="space-y-0.5">{blocks}</div>;
}

type AiPanelProps = {
  currentPage: Page;
  onNavigate: (page: string) => void;
  onOpenModelSelector?: () => void;
  onEnterProject: (projectId: string, page?: string) => Promise<void>;
};

function projectChartStorageKey(projectId: string | null | undefined) {
  return `methodpal_project_charts_v070:${getCurrentUserId()}:${projectId || "no_project"}`;
}

function saveChartForProject(projectId: string | null | undefined, chart: any) {
  if (!chart) return;
  try {
    const key = projectChartStorageKey(projectId);
    const old = JSON.parse(localStorage.getItem(key) || "[]");
    const list = Array.isArray(old) ? old : [];
    const next = [
      chart,
      ...list.filter(
        (c: any) =>
          c?.chart_id !== chart.chart_id && c?.chart_type !== chart.chart_type,
      ),
    ].slice(0, 30);
    localStorage.setItem(key, JSON.stringify(next));
  } catch {
    // best-effort local chart cache
  }
}

function formatTransformSummary(res: any, applied = false) {
  const s = res?.summary || {};
  const lines: string[] = [];
  lines.push(applied ? `### 数据转换已应用` : `### 数据转换预览`);
  if (res?.message) lines.push(res.message);
  if (s.before_rows !== undefined || s.after_rows !== undefined) {
    lines.push(`- 行数：${s.before_rows ?? "—"} → ${s.after_rows ?? "—"}`);
  }
  if (s.before_columns !== undefined || s.after_columns !== undefined) {
    lines.push(
      `- 列数：${s.before_columns ?? "—"} → ${s.after_columns ?? "—"}`,
    );
  }
  if (s.new_column || s.added_columns?.length) {
    lines.push(`- 新增变量：${s.new_column || s.added_columns.join("、")}`);
  }
  if (s.removed_rows !== undefined) {
    lines.push(`- 删除行数：${s.removed_rows}`);
  }
  if (s.changed_cells !== undefined) {
    lines.push(`- 修改单元格：${s.changed_cells}`);
  }
  if (s.counts) {
    lines.push(
      `- 转换后类别：0 = ${s.counts["0"] ?? 0} 条，1 = ${s.counts["1"] ?? 0} 条，缺失 = ${s.counts.missing ?? 0} 条`,
    );
  }
  if (Array.isArray(res?.warnings) && res.warnings.length) {
    lines.push("\n**注意：**");
    res.warnings.slice(0, 4).forEach((w: string) => lines.push(`- ${w}`));
  }
  return lines.join("\n");
}

export function AiPanel({
  currentPage,
  onNavigate,
  onOpenModelSelector,
  onEnterProject,
}: AiPanelProps) {
  const workspace = useWorkspace();
  const {
    userId,
    projects,
    activeProject,
    dataset,
    selectedVariables,
    selectedModel,
    modelOptions,
    dataReviewStatus,
    datasetVersion,
    analysisResult,
    setDataset,
    setSelectedVariables,
    setSelectedModel,
    setAnalysisResult,
  } = workspace;

  const isLobby = currentPage === "home";
  const projectId = isLobby ? "no_project" : activeProject?.project_id || "no_project";
  const storageKey = useMemo(
    () =>
      safeStorageKey(userId || "", isLobby ? null : activeProject?.project_id || null),
    [userId, activeProject?.project_id, isLobby],
  );

  const [messages, setMessages] = useState<Message[]>(() =>
    isLobby
      ? [buildLobbyMessage(projects || [], activeProject)]
      : [initialMessage(currentPage, activeProject?.name, !!dataset)],
  );
  const [input, setInput] = useState("");
  const [isTyping, setIsTyping] = useState(false);
  const [loadedKey, setLoadedKey] = useState(storageKey);
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const [agentMode, setAgentMode] = useState<AgentMode>("assisted");
  const [workflowState, setWorkflowState] = useState<any>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const mountedRef = useRef(true);
  const requestSequenceRef = useRef(0);
  const chatScopeRef = useRef(storageKey);
  chatScopeRef.current = storageKey;
  const scopeAlive = () => mountedRef.current && chatScopeRef.current === storageKey && getCurrentUserId() === userId;

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; requestSequenceRef.current += 1; };
  }, []);

  useEffect(() => {
    let cancelled = false;
    requestSequenceRef.current += 1;
    setIsTyping(false);
    setHistoryLoaded(false);

    async function loadHistory() {
      try {
        await waitForChatHistory(userId, projectId);
        if (cancelled) return;
        const [history, state] = await Promise.all([
          api.getAgentHistory(projectId),
          api.getAgentState(projectId).catch(() => null),
        ]);
        let saved = normalizeMessages(history.messages);
        // A failed previous save must not discard the newest local messages
        // merely because the server successfully returned an older snapshot.
        try {
          const unsaved = localStorage.getItem(`${storageKey}:pending`);
          if (unsaved) saved = normalizeMessages(JSON.parse(unsaved));
        } catch {}
        if (!cancelled) {
          setMessages(
            saved.length
              ? saved
              : [isLobby ? buildLobbyMessage(projects || [], activeProject) : initialMessage(currentPage, activeProject?.name, !!dataset)],
          );
          setWorkflowState(isLobby ? {stage:"lobby",stage_label:"智能对话",mode:"assisted"} : state);
          if (state?.mode === "autopilot" || state?.mode === "assisted")
            setAgentMode(state.mode);
        }
      } catch {
        try {
          const raw = localStorage.getItem(storageKey);
          const saved = raw ? normalizeMessages(JSON.parse(raw)) : [];
          if (!cancelled)
            setMessages(
              saved.length
                ? saved
                : [isLobby ? buildLobbyMessage(projects || [], activeProject) : initialMessage(currentPage, activeProject?.name, !!dataset)],
            );
        } catch {
          if (!cancelled)
            setMessages([
              isLobby ? buildLobbyMessage(projects || [], activeProject) : initialMessage(currentPage, activeProject?.name, !!dataset),
            ]);
        }
      } finally {
        if (!cancelled) {
          setLoadedKey(storageKey);
          setHistoryLoaded(true);
        }
      }
    }

    loadHistory();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    storageKey,
    projectId,
    isLobby,
  ]);

  useEffect(() => {
    if (!historyLoaded || loadedKey !== storageKey) return;

    const snapshot = messages.slice(-80);
    const serialized = JSON.stringify(snapshot);

    try {
      localStorage.setItem(storageKey, serialized);
      localStorage.setItem(`${storageKey}:pending`, serialized);
    } catch {
      // localStorage quota can fail silently for long chats
    }

    void persistChatHistory(userId, projectId, snapshot).then(() => {
      try {
        if (getCurrentUserId() === userId && localStorage.getItem(`${storageKey}:pending`) === serialized)
          localStorage.removeItem(`${storageKey}:pending`);
      } catch {}
    }).catch(() => {
      // localStorage remains an owner/scope-specific fallback on network failure.
    });
  }, [messages, loadedKey, storageKey, historyLoaded, projectId, isLobby]);

  useEffect(() => {
    if (isLobby || !historyLoaded || loadedKey !== storageKey) return;
    if (!dataset?.dataset_id) return;
    setMessages((prev) => {
      const exists = prev.some(
        (m) =>
          m.meta?.kind === "dataset_summary" &&
          m.meta?.dataset_id === dataset.dataset_id,
      );
      if (exists) return prev;
      return [...prev, buildDatasetSummaryMessage(dataset)];
    });
  }, [dataset?.dataset_id, isLobby, historyLoaded, loadedKey, storageKey]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isTyping]);

  function buildAgentContext() {
    if (isLobby) {
      return {
        page: "home",
        project_id: null,
        projects: (projects || []).map((p: any) => ({
          project_id: p.project_id,
          name: p.name,
          progress: p.progress,
          research_goal: p.research_goal,
          dataset_id: p.dataset_id,
          analysis_id: p.analysis_id,
        })),
      };
    }
    return {
      project_id: activeProject?.project_id || null,
      project_name: activeProject?.name || null,
      research_goal:
        activeProject?.research_goal || activeProject?.description || "",
      analysis_goal:
        activeProject?.analysis_goal || selectedVariables?.analysis_goal || null,
      research_spec: activeProject?.research_spec || null,
      dataset_id: dataset?.dataset_id || activeProject?.dataset_id || null,
      dataset_summary: datasetSummaryForAgent(dataset),
      variable_options: variableOptionsFromDataset(dataset, selectedVariables),
      analysis_id:
        analysisResult?.analysis_id || activeProject?.analysis_id || null,
      page: currentPage,
      selected_variables: selectedVariables,
      selected_model: selectedModel,
      model_options: modelOptions,
      data_review_status: dataReviewStatus,
      dataset_version: datasetVersion,
      analysis_result: analysisResult,
    };
  }

  function appendNeedDataMessage() {
    setMessages((prev) => [
      ...prev,
      {
        role: "ai",
        content:
          "当前项目还没有上传数据。请先上传 CSV/XLSX，之后我才能基于真实字段推荐变量类型、变量角色和分析模型。",
        time: now(),
        actions: [
          {
            type: "navigate",
            id: "go_upload",
            label: "去上传数据",
            payload: { page: "upload" },
          },
        ],
        meta: { kind: "guard_no_dataset" },
      },
    ]);
  }

  function appendNeedModelMessage() {
    setMessages((prev) => [
      ...prev,
      {
        role: "ai",
        content:
          "当前项目还没有选择分析模型。现在的流程允许先选模型，再根据该模型的 requirement 配置变量角色。",
        time: now(),
        actions: [
          {
            type: "agent",
            id: "recommend_methods",
            label: "让 Agent 推荐模型",
            payload: { action: "recommend_methods" },
          },
          {
            type: "open_model_selector",
            id: "go_models",
            label: "打开模型选择",
            payload: {},
          },
          {
            type: "agent",
            id: "autopilot",
            label: "自动完成基础分析",
            payload: { action: "autopilot", mode: "autopilot" },
          },
        ],
        meta: { kind: "guard_no_model" },
      },
    ]);
  }

  function shouldGuard(action: string) {
    if (
      [
        "recommend_variables",
        "recommend_methods",
        "execute",
        "autopilot",
      ].includes(action) &&
      !dataset
    ) {
      appendNeedDataMessage();
      return true;
    }
    if (["recommend_variables", "execute"].includes(action) && !selectedModel) {
      appendNeedModelMessage();
      return true;
    }
    return false;
  }

  function applyAgentResponseSideEffects(res: any, action: string) {
    if (action === "chat") return;
    if (res?.workflow_state) {
      setWorkflowState(res.workflow_state);
      if (
        res.workflow_state.mode === "autopilot" ||
        res.workflow_state.mode === "assisted"
      ) {
        setAgentMode(res.workflow_state.mode);
      }
      if (
        res.workflow_state.selected_model &&
        res.workflow_state.selected_model !== selectedModel
      ) {
        setSelectedModel(
          res.workflow_state.selected_model,
          res.workflow_state.model_options || {},
        );
      }
      if (
        res.workflow_state.auto_applied_variables &&
        res.workflow_state.selected_variables
      ) {
        setSelectedVariables(res.workflow_state.selected_variables);
      }
    }
    if (res?.execution_result) {
      setAnalysisResult(res.execution_result);
      if (["autopilot", "resume", "execute"].includes(action)) onNavigate("results");
    }
  }

  async function runAgent(
    action: string,
    message: string,
    options: Record<string, any> = {},
    overrideMode?: AgentMode,
  ) {
    if (shouldGuard(action)) return null;

    const requestId = ++requestSequenceRef.current;
    const requestScope = storageKey;

    const mode =
      overrideMode || (action === "autopilot" ? "autopilot" : agentMode);
    setIsTyping(true);
    try {
      const payload = {
        action,
        message,
        mode,
        thread_id: options.thread_id || (action === "resume" ? workflowState?.thread_id : null),
        answers: options.answers || {},
        context: buildAgentContext(),
        options: { ...options, mode, ...(action === "chat" ? {conversation_history: messages.slice(-8).map(m => ({role:m.role,content:m.content}))} : {}) },
      };
      const res =
        action === "resume" && (api as any).agentResume
          ? await (api as any).agentResume(payload)
          : await api.agentMessage(payload);
      // A slow answer belongs to the account/project that requested it, not
      // whichever workspace the user opened while waiting.
      if (!mountedRef.current || requestSequenceRef.current !== requestId || chatScopeRef.current !== requestScope) return null;
      applyAgentResponseSideEffects(res, action);
      setMessages((prev) => [
        ...prev,
        {
          role: "ai",
          content: res.message || "我已经完成处理。",
          actions: res.actions || [],
          time: now(),
          meta: { workflow_state: res.workflow_state, trace: res.trace || [], source: action === "chat" ? res.source : undefined, knowledge_sources: res.knowledge_sources || [], warnings: res.warnings || [] },
        },
      ]);
      return res;
    } catch (e: any) {
      if (!mountedRef.current || requestSequenceRef.current !== requestId || chatScopeRef.current !== requestScope) return null;
      const reason = e?.message || "Agent 暂不可用";
      setMessages((prev) => [
        ...prev,
        {
          role: "ai",
          content: `我暂时没能调用 Agent 工具。错误：${reason}`,
          time: now(),
        },
      ]);
      return null;
    } finally {
      if (mountedRef.current && requestSequenceRef.current === requestId && chatScopeRef.current === requestScope) setIsTyping(false);
    }
  }

  async function resetChatHistory() {
    if (!historyLoaded || isTyping) return;
    requestSequenceRef.current += 1;
    const fresh = [isLobby ? buildLobbyMessage(projects || [], activeProject) : initialMessage(currentPage, activeProject?.name, !!dataset)];
    setMessages(fresh);
    setWorkflowState(isLobby ? {stage:"lobby",stage_label:"工作台接待",mode:"assisted"} : null);
    try {
      localStorage.setItem(storageKey, JSON.stringify(fresh));
      await persistChatHistory(userId, projectId, fresh);
      if (!isLobby) await api.clearAgentState(projectId);
    } catch {}
  }

  async function sendMessage() {
    const text = input.trim();
    if (!text || isTyping || !historyLoaded) return;
    setMessages((prev) => [
      ...prev,
      { role: "user", content: text, time: now() },
    ]);
    setInput("");
    // Free text is conversation first, not an implicit command or pause answer.
    await runAgent("chat", text, {}, agentMode);
  }

  async function handleQuickAction(
    action: string,
    label: string,
    modeOverride?: AgentMode,
  ) {
    if (isTyping || !historyLoaded) return;
    setMessages((prev) => [
      ...prev,
      { role: "user", content: label, time: now() },
    ]);
    if (isLobby) {
      await runAgent("chat", label, {}, "assisted");
      return;
    }
    await runAgent(action, label, {}, modeOverride);
  }

  async function handleAction(action: any) {
    if (!action || action.disabled || isTyping || !historyLoaded) return;
    if (action.type === "open_project_form") {
      onNavigate("home");
      setTimeout(() => {
        const form = document.getElementById("methodpal-new-project");
        form?.scrollIntoView({behavior:"smooth",block:"center"});
        if (form instanceof HTMLInputElement) form.focus();
        else form?.querySelector<HTMLInputElement>("input")?.focus();
      }, 100);
      return;
    }
    if (action.type === "clarification_answer") {
      const questionId = action.payload?.question_id;
      const value = action.payload?.value;
      const threadId = action.payload?.thread_id || workflowState?.thread_id;
      if (!questionId || value === undefined || !threadId) return;
      setMessages((prev) => [
        ...prev,
        { role: "user", content: action.label || String(value), time: now() },
      ]);
      await runAgent(
        "resume",
        action.label || String(value),
        { thread_id: threadId, answers: { [questionId]: value } },
        "autopilot",
      );
      return;
    }
    if (action.type === "select_project") {
      const targetId = action.payload?.project_id;
      if (targetId) {
        const target =
          (projects || []).find((p: any) => p.project_id === targetId) ||
          activeProject;
        await onEnterProject(targetId, entryPageForProject(target));
      }
      return;
    }
    if (action.type === "open_model_selector") {
      if (onOpenModelSelector) onOpenModelSelector();
      else onNavigate("models");
      return;
    }
    if (action.type === "select_model") {
      const model =
        action.payload?.model ||
        action.payload?.model_id ||
        action.payload?.method_id;
      if (!model) return;
      const changingModel = Boolean(selectedModel && model !== selectedModel);
      const hasConfiguredRoles = Boolean(
        selectedVariables?.dependent_variable ||
          selectedVariables?.group_variable ||
          selectedVariables?.independent_variables?.length ||
          selectedVariables?.control_variables?.length ||
          selectedVariables?.mediator_variables?.length ||
          selectedVariables?.moderator_variables?.length,
      );
      if (changingModel && hasConfiguredRoles) {
        const confirmed = window.confirm(
          `确定把当前模型更换为“${model}”吗？\n\n通过 Agent 直接更换模型会保留字段类型，但会重新校验并可能清空现有变量角色。也可以取消后打开模型选择窗口查看详细要求。`,
        );
        if (!confirmed) {
          if (onOpenModelSelector) onOpenModelSelector();
          return;
        }
      }
      setSelectedModel(
        model,
        action.payload?.options || action.payload?.model_options || {},
      );
      const ready =
        action.payload?.availability === "ready" ||
        action.payload?.enabled === true;
      setMessages((prev) => [
        ...prev,
        {
          role: "ai",
          content: ready
            ? `已把模型 **${model}** 保存到当前项目。它目前已满足运行要求，请回到数据工作区检查表头变量后开始分析。`
            : `已把模型 **${model}** 保存到当前项目。下一步请回到数据工作区，在每列表头按该模型的 requirement 配置变量角色。`,
          time: now(),
          actions: [
            {
              type: "navigate",
              id: "configure_model_variables",
              label: ready
                ? "回数据工作区检查表头并分析"
                : "回数据工作区配置表头变量",
              payload: { page: "upload" },
            },
          ],
          meta: { kind: "model_selected", model },
        },
      ]);
      onNavigate("upload");
      return;
    }
    if (action.type === "navigate") {
      const page = action.payload?.page;
      if (page === "models") {
        if (onOpenModelSelector) onOpenModelSelector();
        else onNavigate("models");
      } else if (page) {
        onNavigate(page);
      }
      return;
    }
    if (action.type === "agent") {
      const nextAction = action.payload?.action || action.id;
      const nextMode =
        action.payload?.mode === "autopilot" ? "autopilot" : action.payload?.mode === "assisted" ? "assisted" : agentMode;
      setMessages(prev => [...prev, {role:"user",content:action.label || "继续",time:now()}]);
      await runAgent(nextAction, action.payload?.message || action.label || "继续", action.payload?.options || {}, nextMode);
      return;
    }
    if (action.type === "apply_variable_types") {
      const nextTypes =
        action.payload?.variable_types || action.payload?.updates || {};
      const nextSelection = {
        ...(selectedVariables || {
          dependent_variable: "",
          independent_variables: [],
          control_variables: [],
          mediator_variables: [],
          moderator_variables: [],
          group_variable: "",
        }),
        ...(action.payload?.selected_variables || {}),
        variable_types: {
          ...(selectedVariables?.variable_types || {}),
          ...(action.payload?.selected_variables?.variable_types || {}),
          ...nextTypes,
        },
      };
      setSelectedVariables(nextSelection);
      const updateCount = Object.keys(nextTypes || {}).length;
      setMessages((prev) => [
        ...prev,
        {
          role: "ai",
          content: `已应用 ${updateCount} 个变量类型修改，并同步到当前项目。数据表头会显示这些手动类型；后续模型推荐和分析会优先使用它们。`,
          time: now(),
          actions: [
            {
              type: "navigate",
              id: "go_variables_after_type_apply",
              label: "回数据工作区查看表头",
              payload: { page: "upload" },
            },
            {
              type: "agent",
              id: "recommend_methods_after_type_apply",
              label: "基于新类型推荐模型",
              payload: { action: "recommend_methods" },
            },
          ],
        },
      ]);
      return;
    }

    if (action.type === "apply_variables") {
      setSelectedVariables(action.payload);
      setMessages((prev) => [
        ...prev,
        {
          role: "ai",
          content:
            "已把推荐变量设置应用到当前项目。数据工作区会同步更新；如果当前模型的 requirement 已满足，下一步即可运行分析。",
          time: now(),
          actions: [
            {
              type: "navigate",
              id: "go_workspace_after_apply",
              label: "回数据工作区检查并运行",
              payload: { page: "upload" },
            },
            {
              type: "navigate",
              id: "go_variables_after_apply",
              label: "回数据工作区查看表头配置",
              payload: { page: "upload" },
            },
          ],
        },
      ]);
      return;
    }
    if (action.type === "preview_transform") {
      if (!dataset?.dataset_id) {
        appendNeedDataMessage();
        return;
      }
      const operation = action.payload?.operation || action.payload;
      setIsTyping(true);
      try {
        const res = await api.previewDatasetTransform(dataset.dataset_id, {
          operation,
          selected_variables: selectedVariables,
          create_new_dataset: true,
        });
        if (!scopeAlive()) return;
        setMessages((prev) => [
          ...prev,
          {
            role: "ai",
            content: formatTransformSummary(res, false),
            time: now(),
            actions: [
              {
                type: "apply_transform",
                id: "apply_transform_after_preview",
                label: "应用并重新诊断",
                payload: { operation },
              },
              {
                type: "navigate",
                id: "go_upload_after_preview",
                label: "回数据上传页查看",
                payload: { page: "upload" },
              },
            ],
            meta: { kind: "transform_preview", preview: res },
          },
        ]);
      } catch (e: any) {
        setMessages((prev) => [
          ...prev,
          {
            role: "ai",
            content: `数据转换预览失败：${e?.message || e}`,
            time: now(),
          },
        ]);
      } finally {
        setIsTyping(false);
      }
      return;
    }

    if (action.type === "apply_transform") {
      if (!dataset?.dataset_id) {
        appendNeedDataMessage();
        return;
      }
      const operation = action.payload?.operation || action.payload;
      setIsTyping(true);
      try {
        const res = await api.applyDatasetTransform(dataset.dataset_id, {
          operation,
          selected_variables: selectedVariables,
          create_new_dataset: true,
        });
        if (!scopeAlive()) return;
        if (res?.dataset) {
          setDataset(res.dataset);
        }
        if (res?.variable_selection_patch) {
          // setDataset resets the old selection; re-apply the patch on the next tick.
          setTimeout(
            () => { if (scopeAlive()) setSelectedVariables(res.variable_selection_patch); },
            0,
          );
        }
        setMessages((prev) => [
          ...prev,
          {
            role: "ai",
            content:
              formatTransformSummary(res, true) +
              "\n\n我已经把新数据版本载入当前项目。旧变量配置和分析结果已失效；请先检查数据，再选择模型并按 requirement 配置变量。",
            time: now(),
            actions: [
              {
                type: "agent",
                id: "recommend_methods_after_transform",
                label: "基于新数据推荐模型",
                payload: { action: "recommend_methods" },
              },
              {
                type: "open_model_selector",
                id: "go_models_after_transform",
                label: "打开模型选择",
                payload: {},
              },
              {
                type: "navigate",
                id: "go_upload_after_transform",
                label: "查看数据工作区",
                payload: { page: "upload" },
              },
            ],
            meta: { kind: "transform_apply", result: res },
          },
        ]);
      } catch (e: any) {
        setMessages((prev) => [
          ...prev,
          {
            role: "ai",
            content: `数据转换应用失败：${e?.message || e}`,
            time: now(),
          },
        ]);
      } finally {
        setIsTyping(false);
      }
      return;
    }

    if (action.type === "generate_chart") {
      const chartType = action.payload?.chart_type;
      const payloadAnalysisId =
        action.payload?.analysis_id ||
        analysisResult?.analysis_id ||
        activeProject?.analysis_id ||
        null;
      const payloadDatasetId =
        action.payload?.dataset_id ||
        dataset?.dataset_id ||
        activeProject?.dataset_id ||
        null;
      if (!chartType) {
        setMessages((prev) => [
          ...prev,
          {
            role: "ai",
            content: "图表生成失败：缺少 chart_type。",
            time: now(),
          },
        ]);
        return;
      }
      setIsTyping(true);
      try {
        const res = await api.generateChart({
          analysis_id: payloadAnalysisId,
          dataset_id: payloadDatasetId,
          chart_type: chartType,
          options: { ...(action.payload?.options || {}), top_n: 15 },
        });
        if (!scopeAlive()) return;
        saveChartForProject(activeProject?.project_id, res.chart);
        setMessages((prev) => [
          ...prev,
          {
            role: "ai",
            content: `已生成图表：**${res.chart?.title || chartType}**。我已经把它保存到当前项目的图表分析页。`,
            time: now(),
            actions: [
              {
                type: "navigate",
                id: "go_charts_after_generate",
                label: "查看图表分析",
                payload: { page: "charts" },
              },
            ],
            meta: { kind: "chart_generated", chart: res.chart },
          },
        ]);
        onNavigate("charts");
      } catch (e: any) {
        setMessages((prev) => [
          ...prev,
          {
            role: "ai",
            content: `图表生成失败：${e?.message || e}`,
            time: now(),
          },
        ]);
      } finally {
        setIsTyping(false);
      }
      return;
    }

    if (action.type === "run_model_and_generate_chart") {
      const model = action.payload?.model;
      const chartType = action.payload?.chart_type;
      const selected = action.payload?.selected_variables || selectedVariables;
      if (!model || !chartType) {
        setMessages((prev) => [
          ...prev,
          {
            role: "ai",
            content: "无法执行：缺少模型或图表类型。",
            time: now(),
          },
        ]);
        return;
      }
      if (selected) setSelectedVariables(selected);
      setSelectedModel(model, action.payload?.options || modelOptions);
      const res = await runAgent(
        "execute",
        `运行 ${model} 并生成图表`,
        {
          model,
          selected_variables: selected,
        },
        "assisted",
      );
      const newAnalysis = res?.execution_result;
      const newAnalysisId = newAnalysis?.analysis_id;
      if (newAnalysis) setAnalysisResult(newAnalysis);
      if (!newAnalysisId) return;
      setIsTyping(true);
      try {
        const chartRes = await api.generateChart({
          analysis_id: newAnalysisId,
          dataset_id: dataset?.dataset_id || activeProject?.dataset_id || null,
          chart_type: chartType,
          options: { ...(action.payload?.options || {}), top_n: 15 },
        });
        if (!scopeAlive()) return;
        saveChartForProject(activeProject?.project_id, chartRes.chart);
        setMessages((prev) => [
          ...prev,
          {
            role: "ai",
            content: `模型已运行，并已生成图表：**${chartRes.chart?.title || chartType}**。`,
            time: now(),
            actions: [
              {
                type: "navigate",
                id: "go_charts_after_model_chart",
                label: "查看图表分析",
                payload: { page: "charts" },
              },
              {
                type: "navigate",
                id: "go_results_after_model_chart",
                label: "查看新分析结果",
                payload: { page: "results" },
              },
            ],
            meta: { kind: "model_chart_generated", chart: chartRes.chart },
          },
        ]);
        onNavigate("charts");
      } catch (e: any) {
        setMessages((prev) => [
          ...prev,
          {
            role: "ai",
            content: `模型已运行，但图表生成失败：${e?.message || e}`,
            time: now(),
          },
        ]);
      } finally {
        setIsTyping(false);
      }
      return;
    }

    if (action.type === "run_analysis") {
      const model = action.payload?.model;
      const selected = action.payload?.selected_variables || selectedVariables;
      if (selected) setSelectedVariables(selected);
      if (model)
        setSelectedModel(model, action.payload?.options || modelOptions);
      const res = await runAgent(
        "execute",
        `运行模型：${model || selectedModel || "所选模型"}`,
        {
          model,
          selected_variables: selected,
        },
        "assisted",
      );
      if (res?.execution_result) {
        setAnalysisResult(res.execution_result);
        onNavigate("results");
      }
    }
  }

  const ctx = contextInfo[currentPage] || {
    label: "当前页面",
    hint: "我会根据当前项目状态提供帮助",
  };
  const prompts = quickPrompts[currentPage] || [
    "下一步建议",
    "解释当前页面",
    "我能做什么？",
  ];

  const agentButtons = isLobby
    ? [
        {
          action: "lobby_projects",
          label: "查看项目",
          icon: ListChecks,
          mode: "assisted" as AgentMode,
        },
        {
          action: "lobby_intro",
          label: "项目简介",
          icon: FolderOpen,
          mode: "assisted" as AgentMode,
        },
        {
          action: "lobby_help",
          label: "能做什么",
          icon: Sparkles,
          mode: "assisted" as AgentMode,
        },
      ]
    : [
        {
          action: "plan",
          label: "下一步建议",
          icon: ListChecks,
          mode: "assisted" as AgentMode,
        },
        {
          action: "recommend_methods",
          label: "推荐模型",
          icon: Wand2,
          mode: "assisted" as AgentMode,
        },
        {
          action: "recommend_variables",
          label: "配置变量",
          icon: SlidersHorizontal,
          mode: "assisted" as AgentMode,
        },
        {
          action: "recommend_charts",
          label: "推荐图表",
          icon: Sparkles,
          mode: "assisted" as AgentMode,
        },
        {
          action: "autopilot",
          label: "自动完成",
          icon: PlayCircle,
          mode: "autopilot" as AgentMode,
        },
      ];

  const modeLabel = isLobby
    ? "工作台接待"
    : agentMode === "autopilot"
      ? "自动分析"
      : "交互确认";
  const stageLabel = isLobby
    ? "工作台接待"
    : workflowState?.stage_label || (dataset ? "数据已读取" : "等待上传数据");

  return (
    <div className="flex flex-col h-full bg-card">
      <div className="px-4 py-3.5 border-b border-border bg-secondary/40 shrink-0">
        <div className="flex items-center gap-2 mb-1">
          <div className="w-6 h-6 rounded-md bg-primary flex items-center justify-center shrink-0">
            <Sparkles size={12} className="text-primary-foreground" />
          </div>
          <span className="text-sm font-semibold text-foreground">
            {isLobby ? "工作台接待助手" : "项目研究助手"}
          </span>
          <button
            onClick={resetChatHistory}
            disabled={!historyLoaded || isTyping}
            className="ml-auto inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
            title={
              isLobby ? "仅清空工作台对话，不影响项目对话" : "仅清空当前项目对话，不影响其他项目和工作台"
            }
          >
            <Trash2 size={11} /> 清空
          </button>
          <div className="w-2 h-2 rounded-full bg-emerald-400" title="在线" />
        </div>
        <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <span className="bg-accent text-accent-foreground px-1.5 py-0.5 rounded text-xs">
            {ctx.label}
          </span>
          <span className="truncate">{ctx.hint}</span>
        </div>
        <p className="mt-1 text-[11px] text-muted-foreground truncate">独立对话：{isLobby ? "工作台" : activeProject?.name} · 不与其他项目混合</p>
        <div className="mt-2 flex items-center gap-1.5 text-xs">
          {!isLobby && (
            <>
              <button
                onClick={() => setAgentMode("assisted")}
                className={`px-2 py-1 rounded-md border transition-colors ${agentMode === "assisted" ? "bg-primary text-primary-foreground border-primary" : "bg-card border-border text-muted-foreground hover:bg-muted"}`}
              >
                交互确认
              </button>
              <button
                onClick={() => setAgentMode("autopilot")}
                className={`px-2 py-1 rounded-md border transition-colors ${agentMode === "autopilot" ? "bg-primary text-primary-foreground border-primary" : "bg-card border-border text-muted-foreground hover:bg-muted"}`}
              >
                自动分析
              </button>
            </>
          )}
          <span
            className="ml-auto text-muted-foreground truncate"
            title={stageLabel}
          >
            阶段：{stageLabel}
          </span>
        </div>
      </div>

      <div className="px-3 py-2 border-b border-border bg-background/40 grid grid-cols-2 gap-1.5 shrink-0">
        {agentButtons.map(({ action, label, icon: Icon, mode }) => (
          <button
            key={action}
            onClick={() => handleQuickAction(action, label, mode)}
            disabled={isTyping}
            className="text-left text-xs px-2 py-1.5 rounded-lg border border-border bg-card hover:bg-muted transition-colors flex items-center gap-1.5 disabled:opacity-50"
          >
            <Icon size={12} className="text-primary" />
            <span className="truncate">{label}</span>
          </button>
        ))}
      </div>

      <div className="px-3 py-1.5 border-b border-border bg-secondary/20 text-[11px] text-muted-foreground shrink-0">
        当前模式：{modeLabel}。
        {isLobby
          ? "这里只能查看项目和跳转；进入具体项目后才能执行清洗、变量、模型和报告操作。"
          : agentMode === "autopilot"
            ? "输入目标后会先检查数据和需求；信息不足时会暂停提问，补齐后再自动选模并执行。"
            : "每次只推进一个阶段，关键动作需要你点击确认。"}
      </div>

      <div className="flex-1 overflow-y-auto px-3 py-4 space-y-4 min-h-0">
        {messages.map((msg, i) => (
          <div
            key={i}
            className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
          >
            {msg.role === "ai" && (
              <div className="w-6 h-6 rounded-full bg-primary flex items-center justify-center shrink-0 mt-0.5 mr-2">
                <Sparkles size={10} className="text-primary-foreground" />
              </div>
            )}
            <div
              className={`max-w-[85%] ${msg.role === "user" ? "max-w-[80%]" : ""}`}
            >
              <div
                className={`rounded-2xl px-3 py-2.5 text-xs leading-relaxed ${
                  msg.role === "user"
                    ? "bg-primary text-primary-foreground rounded-tr-sm whitespace-pre-line"
                    : "bg-muted text-foreground rounded-tl-sm"
                }`}
              >
                {msg.role === "ai" ? (
                  <MarkdownMessage content={msg.content} />
                ) : (
                  msg.content
                )}
              </div>

              {msg.role === "ai" && msg.actions && msg.actions.length > 0 && (
                <div className="mt-2 flex flex-col gap-1.5">
                  {msg.actions.map((a) => (
                    <button
                      key={a.id || a.label}
                      onClick={() => handleAction(a)}
                      disabled={a.disabled}
                      title={a.disabled_reason || ""}
                      className="text-left text-xs px-2.5 py-1.5 rounded-lg border border-border bg-card hover:bg-background disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                    >
                      {a.label}
                    </button>
                  ))}
                </div>
              )}

              <p className="text-xs text-muted-foreground mt-1 px-1">
                {msg.time}
              </p>
              {msg.role === "ai" && msg.meta?.source && (
                <div className="text-[10px] text-muted-foreground px-1 mt-1 space-y-1">
                  <p>{msg.meta.source === "llm_rag" ? "LLM + 网站帮助" : msg.meta.source === "llm_chat" ? "LLM 对话" : "帮助检索降级（未获得可用 LLM 回复）"} · 本条仅对话，操作需确认</p>
                  {msg.meta.knowledge_sources?.length > 0 && <p>参考：{msg.meta.knowledge_sources.map((s:any) => s.title).join("、")}</p>}
                  {msg.meta.warnings?.map((w:string,i:number) => <p key={i} className="text-amber-700">{w}</p>)}
                </div>
              )}
            </div>
          </div>
        ))}

        {isTyping && (
          <div className="flex items-start gap-2">
            <div className="w-6 h-6 rounded-full bg-primary flex items-center justify-center shrink-0">
              <Sparkles size={10} className="text-primary-foreground" />
            </div>
            <div className="bg-muted rounded-2xl rounded-tl-sm px-3 py-2.5 flex items-center gap-1">
              {[0, 150, 300].map((d) => (
                <span
                  key={d}
                  className="w-1.5 h-1.5 rounded-full bg-muted-foreground/60 animate-pulse"
                  style={{ animationDelay: `${d}ms` }}
                />
              ))}
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      <div className="px-3 pb-2 shrink-0 border-t border-border pt-2">
        <p className="text-xs text-muted-foreground mb-1.5 flex items-center gap-1">
          <ChevronDown size={10} /> 快捷提问
        </p>
        <div className="flex flex-col gap-1">
          {prompts.map((p) => (
            <button
              key={p}
              onClick={() => {
                setInput(p);
                textareaRef.current?.focus();
              }}
              className="text-left text-xs px-2.5 py-1.5 rounded-lg bg-secondary text-secondary-foreground hover:bg-accent hover:text-accent-foreground transition-colors truncate"
            >
              {p}
            </button>
          ))}
        </div>
      </div>

      <div className="px-3 pb-3 pt-2 shrink-0">
        <div className="flex items-end gap-2 bg-input-background rounded-xl border border-border focus-within:ring-1 focus-within:ring-primary/40 px-3 py-2">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                sendMessage();
              }
            }}
            placeholder={
              isLobby
                ? "问网站用法、统计概念，或说说你的研究问题…"
                : agentMode === "autopilot"
                  ? "告诉我需求；先理解并确认，再进入自动分析…"
                  : "告诉我研究问题，或让 Agent 推荐变量/模型…"
            }
            rows={2}
            className="flex-1 bg-transparent text-xs text-foreground placeholder:text-muted-foreground resize-none focus:outline-none min-h-0"
          />
          <button
            onClick={sendMessage}
            disabled={!input.trim() || isTyping || !historyLoaded}
            className="w-7 h-7 rounded-lg bg-primary flex items-center justify-center shrink-0 hover:opacity-90 disabled:opacity-40 transition-opacity mb-0.5"
          >
            <Send size={13} className="text-primary-foreground" />
          </button>
        </div>
        <p className="text-xs text-muted-foreground mt-1.5 flex items-center gap-1 justify-center">
          <CornerDownLeft size={10} /> Enter 发送 · Shift+Enter 换行
        </p>
      </div>
    </div>
  );
}
