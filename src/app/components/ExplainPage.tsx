import { getCurrentUserId } from "../lib/api";
import { useEffect, useMemo, useRef, useState } from "react";
import { Sparkles, RefreshCw, ChevronRight, AlertCircle, Wand2, Loader2 } from "lucide-react";
import { explain } from "../lib/api";
import { useWorkspace } from "../context/WorkspaceContext";
import { MarkdownView } from "./MarkdownView";

type Props = { onNavigate: (page: string) => void };

type ExplainStyle = "academic" | "plain" | "concise";

type ExplainJobResult = {
  explanation: any;
  ok: boolean;
  message: string;
};

type ExplainJobRecord = {
  promise: Promise<ExplainJobResult>;
  signature: string;
  style: ExplainStyle;
  started_at: string;
};

const explainJobs = new Map<string, ExplainJobRecord>();

function stableStringify(value: any): string {
  try {
    const seen = new WeakSet();
    return JSON.stringify(value, (_key, val) => {
      if (val && typeof val === "object") {
        if (seen.has(val)) return "[Circular]";
        seen.add(val);
        if (!Array.isArray(val)) {
          return Object.keys(val).sort().reduce((acc: any, key) => {
            acc[key] = val[key];
            return acc;
          }, {});
        }
      }
      return val;
    });
  } catch {
    return String(value || "");
  }
}

function shortHash(text: string) {
  let h = 2166136261;
  for (let i = 0; i < text.length; i += 1) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (h >>> 0).toString(16);
}

function explainJobKey(projectId: string | null | undefined, signature: string, style: string) {
  return `methodpal_explain_job_v075:${getCurrentUserId()}:${projectId || "no_project"}:${signature || "no_signature"}:${style || "style"}`;
}

function explainStorageKey(projectId: string | null | undefined, signature: string, style: string) {
  return `methodpal_explain_result_v075:${getCurrentUserId()}:${projectId || "no_project"}:${signature || "no_signature"}:${style || "style"}`;
}

function explainPendingKey(projectId: string | null | undefined, signature: string, style: string) {
  return `methodpal_explain_pending_v075:${getCurrentUserId()}:${projectId || "no_project"}:${signature || "no_signature"}:${style || "style"}`;
}

function saveStoredExplanation(projectId: string | null | undefined, signature: string, style: string, explanation: any) {
  try {
    localStorage.setItem(explainStorageKey(projectId, signature, style), JSON.stringify(explanation));
    localStorage.removeItem(explainPendingKey(projectId, signature, style));
  } catch {
    // local cache only
  }
}

function loadStoredExplanation(projectId: string | null | undefined, signature: string, style: string) {
  try {
    const raw = localStorage.getItem(explainStorageKey(projectId, signature, style));
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function savePendingExplanation(projectId: string | null | undefined, signature: string, style: string) {
  try {
    localStorage.setItem(explainPendingKey(projectId, signature, style), JSON.stringify({ status: "pending", started_at: new Date().toISOString() }));
  } catch {
    // local cache only
  }
}

function loadPendingExplanation(projectId: string | null | undefined, signature: string, style: string) {
  try {
    const raw = localStorage.getItem(explainPendingKey(projectId, signature, style));
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}


function firstString(...values: any[]) {
  for (const value of values) {
    if (Array.isArray(value) && value.length > 0) return String(value[0] || "");
    if (value !== undefined && value !== null && String(value).trim()) return String(value).trim();
  }
  return "";
}

function selectedDependent(selection: any) {
  return firstString(selection?.dependent_variable, selection?.dependent_variables, selection?.target, selection?.y, selection?.outcome_variable);
}

function analysisDependent(analysis: any) {
  return firstString(analysis?.dependent_variable, analysis?.target, analysis?.target_variable, analysis?.y_variable, analysis?.outcome_variable, analysis?.variables?.dependent_variable);
}

function analysisDatasetId(analysis: any) {
  return firstString(analysis?.dataset_id, analysis?.data_id, analysis?.meta?.dataset_id, analysis?.context?.dataset_id);
}

function analysisMatchesCurrentSelection(analysis: any, selection: any, datasetId?: string | null) {
  if (!analysis?.analysis_id) return false;
  const anaDataset = analysisDatasetId(analysis);
  if (anaDataset && datasetId && anaDataset !== datasetId) return false;
  const yAnalysis = analysisDependent(analysis);
  const ySelected = selectedDependent(selection);
  if (yAnalysis && ySelected && yAnalysis !== ySelected) return false;
  return true;
}

export function ExplainPage({ onNavigate }: Props) {
  const {
    activeProject,
    dataset,
    analysisResult,
    selectedVariables,
    aiExplanation,
    setAiExplanation,
  } = useWorkspace();

  const [style, setStyle] = useState<ExplainStyle>("academic");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const projectId = activeProject?.project_id;
  const datasetId = dataset?.dataset_id || null;
  const analysisIsCurrent = analysisMatchesCurrentSelection(analysisResult, selectedVariables, datasetId);
  const analysisId = analysisIsCurrent ? analysisResult?.analysis_id || null : null;
  const analysisForExplain = analysisIsCurrent ? analysisResult : null;

  const explanationContextSignature = useMemo(() => shortHash(stableStringify({
    project_id: projectId || null,
    dataset_id: datasetId || null,
    analysis_id: analysisId || null,
    model: analysisForExplain?.model || analysisForExplain?.model_name || analysisForExplain?.method || null,
    task_type: analysisForExplain?.task_type || analysisForExplain?.problem_type || null,
    dependent_from_analysis: analysisForExplain?.dependent_variable || null,
    selected_variables: selectedVariables ? {
      dependent_variable: selectedVariables.dependent_variable || selectedVariables.dependent_variables || selectedVariables.target || null,
      independent_variables: selectedVariables.independent_variables || selectedVariables.independent_vars || selectedVariables.predictors || [],
      control_variables: selectedVariables.control_variables || selectedVariables.control_vars || [],
      variable_roles: selectedVariables.variable_roles || selectedVariables.roles || null,
      variable_types: selectedVariables.variable_types || selectedVariables.types || null,
    } : null,
  })), [projectId, datasetId, analysisId, analysisForExplain, selectedVariables]);

  useEffect(() => {
    if (aiExplanation?.style === "academic" || aiExplanation?.style === "plain" || aiExplanation?.style === "concise") {
      setStyle(aiExplanation.style);
    }
  }, [aiExplanation?.style]);

  function attachExplainJob(job: ExplainJobRecord) {
    setLoading(true);
    setNotice("AI 解释正在生成中。可以切换页面，回来后会继续显示结果。");
    job.promise.then((result) => {
      if (!mountedRef.current) return;
      if (job.signature !== explanationContextSignature) return;
      setAiExplanation(result.explanation);
      setNotice(result.message);
      setError("");
    }).catch((err: any) => {
      if (!mountedRef.current) return;
      setError(err?.message || "AI 解释失败");
    }).finally(() => {
      if (mountedRef.current && job.signature === explanationContextSignature) setLoading(false);
    });
  }

  useEffect(() => {
    const key = explainJobKey(projectId, explanationContextSignature, style);
    const liveJob = explainJobs.get(key);
    if (liveJob) {
      attachExplainJob(liveJob);
      return;
    }
    const stored = loadStoredExplanation(projectId, explanationContextSignature, style);
    if (stored?.text && stored?.analysis_id === analysisId) {
      setAiExplanation(stored);
      setNotice("已恢复当前分析的 AI 解释缓存。");
      return;
    }
    const pending = loadPendingExplanation(projectId, explanationContextSignature, style);
    if (pending?.status === "pending") {
      setNotice("上一次 AI 解释任务可能仍在进行或已被页面刷新中断。请点击“生成 AI 解释”继续。页面内切换不会丢失任务。 ");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, explanationContextSignature, style, analysisId]);

  async function runExplain(nextStyle = style) {
    if (!analysisId) {
      setError(analysisResult?.analysis_id && !analysisIsCurrent ? "当前变量/数据上下文已变化，旧分析结果已过期。请先重新运行模型，再生成 AI 解释。" : "请先运行分析，再生成 AI 解释。");
      return;
    }
    const key = explainJobKey(projectId, explanationContextSignature, nextStyle);
    const existing = explainJobs.get(key);
    if (existing) {
      attachExplainJob(existing);
      return;
    }
    setLoading(true);
    setError("");
    setNotice("AI 解释正在生成中。可以切换页面，回来后会继续显示结果。");
    savePendingExplanation(projectId, explanationContextSignature, nextStyle);
    const promise: Promise<ExplainJobResult> = explain({
      style: nextStyle,
      analysis_id: analysisId,
      context_signature: explanationContextSignature,
      context: {
        project_id: projectId || null,
        dataset_id: datasetId || null,
        selected_variables: selectedVariables,
        analysis_result: analysisForExplain,
      },
    }).then((res) => {
      const explanation = {
        ...res,
        style: nextStyle,
        analysis_id: analysisId,
        context_signature: explanationContextSignature,
        artifact_status: "current",
        generated_at: new Date().toISOString(),
      };
      saveStoredExplanation(projectId, explanationContextSignature, nextStyle, explanation);
      return { explanation, ok: true, message: "AI 解释已生成并保存为当前分析解释。" };
    }).finally(() => {
      explainJobs.delete(key);
    });
    const record: ExplainJobRecord = { promise, signature: explanationContextSignature, style: nextStyle, started_at: new Date().toISOString() };
    explainJobs.set(key, record);
    attachExplainJob(record);
  }

  if (!activeProject || !dataset || !analysisResult) {
    return (
      <div className="p-6 max-w-4xl">
        <div className="bg-card rounded-2xl border border-border p-8 text-center">
          <h3 className="text-foreground mb-2">还没有可解释的分析结果</h3>
          <p className="text-sm text-muted-foreground mb-4">AI 解释需要真实 analysis_id。请先运行模型。</p>
          <button onClick={() => onNavigate("models")} className="px-4 py-2 rounded-xl bg-primary text-primary-foreground text-sm">打开模型选择</button>
        </div>
      </div>
    );
  }

  const explanation = aiExplanation;
  const hasCurrentExplanation = Boolean(
    explanation?.analysis_id === analysisId &&
    explanation?.context_signature === explanationContextSignature &&
    explanation?.style === style
  );
  const hasStaleExplanation = Boolean(explanation?.analysis_id && !hasCurrentExplanation);
  const generatedAt = explanation?.generated_at
    ? new Date(explanation.generated_at).toLocaleString()
    : "";

  const emptyText = "还没有生成当前分析的 AI 解释。只有点击“生成 AI 解释”或“重新生成并替换”时才会调用 DeepSeek。";
  const loadingText = "AI 正在基于后端真实统计结果生成解释... 可以切换到其他页面，回来后继续显示。";

  return (
    <div className="p-6 space-y-6 max-w-5xl">
      <div className="rounded-2xl bg-gradient-to-br from-primary to-violet-500 p-6 text-primary-foreground relative overflow-hidden">
        <div className="relative">
          <div className="flex items-center gap-2 mb-2"><Sparkles size={18} /><span className="text-sm opacity-90">DeepSeek 结果解释</span></div>
          <h2 className="text-xl mb-1">AI 解释当前真实分析结果</h2>
          <p className="text-sm opacity-75">项目：{activeProject.name}；数据集：{dataset.name}；分析 ID：{analysisId || "旧分析已过期"}</p>
          {analysisResult?.analysis_id && !analysisIsCurrent && <p className="text-xs opacity-90 mt-2">检测到变量/数据上下文变化，旧分析结果已标记为过期。请重新运行模型后再生成解释。</p>}
        </div>
      </div>

      <div className="bg-card rounded-2xl border border-border p-5">
        <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
          <div>
            <div className="flex gap-2">
              {[
                ["academic", "学术写法"],
                ["plain", "通俗解释"],
                ["concise", "简洁版"],
              ].map(([id, label]) => (
                <button
                  key={id}
                  onClick={() => setStyle(id as ExplainStyle)}
                  className={`px-3 py-1.5 rounded-lg text-xs border ${style === id ? "bg-primary text-primary-foreground border-primary" : "border-border text-muted-foreground hover:bg-muted"}`}
                >
                  {label}
                </button>
              ))}
            </div>
            <p className="mt-2 text-xs text-muted-foreground">
              {hasCurrentExplanation
                ? `已保存当前分析的 AI 解释${generatedAt ? ` · ${generatedAt}` : ""}。切换到本页不会自动重新生成。`
                : hasStaleExplanation
                  ? "检测到旧 AI 解释，但变量/数据/模型上下文已经变化。旧解释不会作为当前结果使用，请重新生成。"
                  : "当前分析还没有 AI 解释。点击按钮后才会生成并保存。"}
            </p>
          </div>

          <div className="flex gap-2">
            {hasCurrentExplanation && (
              <button
                onClick={() => setAiExplanation(null)}
                disabled={loading}
                className="px-3 py-1.5 rounded-lg border border-border text-xs disabled:opacity-50"
              >
                清空解释
              </button>
            )}
            <button
              onClick={() => runExplain(style)}
              disabled={loading}
              className="flex items-center gap-2 px-3 py-1.5 rounded-lg border border-border text-xs disabled:opacity-50"
            >
              {loading ? <Loader2 size={13} className="animate-spin" /> : hasCurrentExplanation ? <RefreshCw size={13} /> : <Wand2 size={13} />}
              {loading ? "生成中..." : hasCurrentExplanation ? "重新生成并替换" : "生成 AI 解释"}
            </button>
          </div>
        </div>

        {notice && <div className="mb-4 rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-xs text-blue-700 flex gap-2"><Sparkles size={14} />{notice}</div>}
        {error && <div className="mb-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700 flex gap-2"><AlertCircle size={14} />{error}</div>}

        <div className="rounded-xl bg-muted/40 p-5 min-h-52 text-foreground">
          {loading ? (
            <p className="text-sm leading-7 text-muted-foreground">{loadingText}</p>
          ) : hasCurrentExplanation ? (
            <MarkdownView content={explanation?.text || ""} />
          ) : hasStaleExplanation ? (
            <div className="space-y-3">
              <p className="text-sm leading-7 text-amber-700">旧解释已标记为过期，因为当前变量、数据集或模型上下文已变化。</p>
              <div className="rounded-lg border border-border bg-background/80 p-4 opacity-70 max-h-60 overflow-auto">
                <MarkdownView content={explanation?.text || ""} />
              </div>
            </div>
          ) : (
            <p className="text-sm leading-7 text-muted-foreground">{emptyText}</p>
          )}
        </div>
      </div>

      {hasCurrentExplanation && explanation?.key_findings?.length > 0 && (
        <div className="grid md:grid-cols-3 gap-3">
          {explanation.key_findings.map((item: any, i: number) => (
            <div key={i} className="bg-card rounded-xl border border-border p-4">
              <p className="text-xs text-muted-foreground">{item.label}</p>
              <p className="text-sm font-medium text-foreground mt-1">{item.value}</p>
            </div>
          ))}
        </div>
      )}

      {hasCurrentExplanation && explanation?.warnings?.length > 0 && (
        <div className="bg-amber-50 border border-amber-200 rounded-xl p-4 text-xs text-amber-700">
          {explanation.warnings.map((w: string, i: number) => <p key={i}>• {w}</p>)}
        </div>
      )}

      <button onClick={() => onNavigate("report")} className="flex items-center gap-2 text-sm bg-primary text-primary-foreground px-5 py-2.5 rounded-xl hover:opacity-90 transition-opacity">
        进入图表与报告 <ChevronRight size={16} />
      </button>
    </div>
  );
}
