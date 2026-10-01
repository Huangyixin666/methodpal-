import { getCurrentUserId } from "../lib/api";
import { listCharts, listReports, saveReportDraft, downloadArtifact } from '../lib/api';
import { currentAnalysis, currentChart } from '../lib/artifactVersions';
import { useEffect, useMemo, useRef, useState } from "react";
import {
  BarChart2,
  CheckSquare,
  Clipboard,
  Copy,
  Download,
  FileText,
  Loader2,
  RefreshCw,
  Sparkles,
  Square,
} from "lucide-react";
import { ChartRenderer } from "./ChartRenderer";
import { MarkdownView } from "./MarkdownView";
import { generateChart, generateReport, recommendCharts } from "../lib/api";
import type { ChartRecommendation, ChartSpec } from "../lib/api";
import { useWorkspace } from "../context/WorkspaceContext";

function projectChartStorageKey(projectId: string | null | undefined) {
  return `methodpal_project_charts_v070:${getCurrentUserId()}:${projectId || "no_project"}`;
}

function loadProjectCharts(projectId: string | null | undefined): ChartSpec[] {
  try {
    const raw = localStorage.getItem(projectChartStorageKey(projectId));
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function saveProjectCharts(projectId: string | null | undefined, charts: ChartSpec[]) {
  try {
    localStorage.setItem(projectChartStorageKey(projectId), JSON.stringify((charts || []).slice(0, 30)));
  } catch {
    // local cache only
  }
}

function chartAnalysisId(chart: ChartSpec | any) {
  return String(chart?.analysis_id || chart?.meta?.analysis_id || "");
}

function chartDatasetId(chart: ChartSpec | any) {
  const aid = chartAnalysisId(chart);
  if (aid.startsWith("dataset:")) return aid.slice("dataset:".length);
  return String(chart?.dataset_id || chart?.meta?.dataset_id || "");
}

function chartMatchesCurrentContext(chart: ChartSpec | any, analysisId?: string | null, datasetId?: string | null) {
  const aid = chartAnalysisId(chart);
  const did = chartDatasetId(chart);
  if (aid.startsWith("dataset:") || did) return Boolean(datasetId && did === datasetId);
  if (analysisId) return !aid || aid === analysisId;
  return !aid;
}

function chartContextKey(chart: ChartSpec | any) {
  return `${chart?.chart_type || "chart"}:${chartAnalysisId(chart) || chartDatasetId(chart) || "no_context"}`;
}

function shortHash(text: string) {
  let h = 2166136261;
  for (let i = 0; i < text.length; i += 1) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (h >>> 0).toString(16);
}

function stableStringify(value: any): string {
  try {
    const seen = new WeakSet();
    return JSON.stringify(value, (_key, val) => {
      if (val && typeof val === "object") {
        if (seen.has(val)) return "[Circular]";
        seen.add(val);
        if (!Array.isArray(val)) {
          return Object.keys(val)
            .sort()
            .reduce((acc: any, k) => {
              acc[k] = val[k];
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

function mergeChart(prev: ChartSpec[], chart: ChartSpec) {
  const key = chartContextKey(chart);
  return [chart, ...prev.filter((c) => c.chart_id !== chart.chart_id && chartContextKey(c) !== key)].slice(0, 30);
}

function chartLabel(chart: ChartSpec) {
  return chart.title || chart.chart_type || chart.chart_id;
}

function markdownFileName(projectName?: string | null) {
  const safe = (projectName || "methodpal_report").replace(/[\\/:*?"<>|\s]+/g, "_").slice(0, 50);
  return `${safe || "methodpal_report"}.md`;
}

function downloadText(filename: string, text: string) {
  const blob = new Blob([text || ""], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function copyText(text: string) {
  if (navigator.clipboard?.writeText) return navigator.clipboard.writeText(text || "");
  const area = document.createElement("textarea");
  area.value = text || "";
  document.body.appendChild(area);
  area.select();
  document.execCommand("copy");
  area.remove();
  return Promise.resolve();
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

function selectedVariableSummary(selection: any) {
  if (!selection) return "当前未保存变量设置。";
  const y = selection.dependent_variable || "未设置";
  const xs = selection.independent_variables || [];
  const cs = selection.control_variables || [];
  return `因变量：${y}；自变量：${xs.length ? xs.join("、") : "未设置"}；控制变量：${cs.length ? cs.join("、") : "无"}。`;
}

function metricSummary(analysis: any) {
  if (!analysis) return "当前还没有分析结果。";
  const pm = analysis.prediction_metrics || analysis.metrics || {};
  const items: string[] = [];
  const keys = [
    ["auc", "AUC"],
    ["test_auc", "测试 AUC"],
    ["accuracy", "准确率"],
    ["test_accuracy", "测试准确率"],
    ["f1", "F1"],
    ["test_f1", "测试 F1"],
    ["r2", "R²"],
    ["test_r2", "测试 R²"],
    ["rmse", "RMSE"],
    ["test_rmse", "测试 RMSE"],
  ];
  for (const [key, label] of keys) {
    const val = pm?.[key] ?? analysis?.[key];
    if (val !== undefined && val !== null && val !== "") items.push(`${label}=${typeof val === "number" ? Number(val).toFixed(4).replace(/0+$/, "").replace(/\.$/, "") : val}`);
  }
  return items.length ? items.join("，") : "当前结果没有提取到常见预测指标。";
}

function localChartBlock(chart: ChartSpec, index: number, target?: string | null) {
  const title = chart.title || chart.chart_type;
  const notes = (chart.notes || []).map((x) => `- ${x}`).join("\n");
  const ctype = String(chart.chart_type || "");
  let guide = "该图用于辅助解释当前分析结果，应结合研究目标、变量定义和模型类型进行审慎表述。";
  if (ctype.includes("feature_importance")) {
    guide = `该图展示模型在预测“${target || "目标变量"}”时对各特征的依赖程度。重要性越高，表示该变量对预测贡献越大，但不等同于统计显著性或因果效应。`;
  } else if (ctype.includes("confusion")) {
    guide = "该图用于检查分类模型的错误结构：对角线代表预测正确，非对角线代表类别混淆。它能帮助判断模型是否偏向多数类或难以区分某些类别。";
  } else if (ctype.includes("correlation")) {
    guide = "该图展示数值变量之间的相关结构，可用于发现潜在共线性或变量聚类关系。相关性不等于因果关系。";
  } else if (ctype.includes("prediction") || ctype.includes("scatter")) {
    guide = "该图检查预测值与真实值的贴合程度。点越接近对角线，预测越接近真实值；偏离明显通常提示拟合或泛化问题。";
  } else if (ctype.includes("descriptive") || ctype.includes("mean")) {
    guide = "该图用于描述数值变量的均值水平。均值大小反映样本中的平均量级，但不同单位变量不能直接比较重要性。";
  }
  return `### 图 ${index}. ${title}\n\n${chart.subtitle || ""}\n\n**读图方式与研究含义。** ${guide}\n\n${notes ? `图表备注：\n${notes}\n` : ""}`;
}

function buildLocalReportDraft(args: {
  projectName?: string | null;
  datasetName?: string | null;
  analysisResult?: any;
  selectedVariables?: any;
  selectedCharts: ChartSpec[];
  style: string;
}) {
  const model = args.analysisResult?.model_name || args.analysisResult?.method || "当前模型";
  const chartBlocks = args.selectedCharts.length
    ? args.selectedCharts.map((chart, i) => localChartBlock(chart, i + 1, args.selectedVariables?.dependent_variable)).join("\n\n")
    : "本报告暂未插入图表。建议先在图表分析页生成相关矩阵、变量重要性、混淆矩阵、模型对比或系数图。";
  return `# ${args.projectName || "MethodPal 分析报告"}\n\n## 1. 数据与研究设计\n\n数据集：${args.datasetName || "当前数据集"}。\n\n${selectedVariableSummary(args.selectedVariables)}\n\n## 2. 分析方法\n\n当前主模型为：${model}。报告风格：${args.style === "plain" ? "通俗解释" : args.style === "concise" ? "简洁摘要" : "学术报告"}。\n\n## 3. 主要结果\n\n${metricSummary(args.analysisResult)}\n\n## 4. 图表结果\n\n${chartBlocks}\n\n## 5. 解释与注意事项\n\n上述图表主要用于展示统计关联、预测贡献或模型诊断。对于 XGBoost、CatBoost、随机森林等机器学习模型，变量重要性表示对预测的贡献，不应直接写作因果效应。对于回归或 Logistic 模型，应结合系数、置信区间、p 值、变量编码方式和稳健性检验进行解释。\n`;
}

function reportStorageKey(projectId: string | null | undefined) {
  return `methodpal_project_report_draft_v073:${getCurrentUserId()}:${projectId || "no_project"}`;
}


type ReportJobResult = {
  report: any;
  content: string;
  ok: boolean;
  message: string;
};

type ReportJobRecord = {
  promise: Promise<ReportJobResult>;
  signature: string;
  started_at: string;
};

const reportJobs = new Map<string, ReportJobRecord>();

function reportJobKey(projectId: string | null | undefined, signature: string) {
  return `methodpal_report_job_v075:${getCurrentUserId()}:${projectId || "no_project"}:${signature || "no_signature"}`;
}

function reportJobStorageKey(projectId: string | null | undefined, signature: string) {
  return `methodpal_report_job_state_v075:${getCurrentUserId()}:${projectId || "no_project"}:${signature || "no_signature"}`;
}

function saveReportJobState(projectId: string | null | undefined, signature: string, payload: any) {
  try {
    localStorage.setItem(reportJobStorageKey(projectId, signature), JSON.stringify(payload));
  } catch {
    // local cache only
  }
}

function loadReportJobState(projectId: string | null | undefined, signature: string) {
  try {
    const raw = localStorage.getItem(reportJobStorageKey(projectId, signature));
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function ReportPage() {
  const { activeProject, dataset, analysisResult, selectedVariables, aiExplanation, selectedModel, modelOptions } = useWorkspace();
  const [history, setHistory] = useState<any[]>([]);
  const [charts, setCharts] = useState<ChartSpec[]>([]);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [recommendations, setRecommendations] = useState<ChartRecommendation[]>([]);
  const [style, setStyle] = useState<"academic" | "plain" | "concise">("academic");
  const [reportMode, setReportMode] = useState<"template" | "ai">("ai");
  const [report, setReport] = useState<any>(null);
  const [content, setContent] = useState<string>("");
  const [previewMode, setPreviewMode] = useState<"preview" | "edit">("preview");
  const [loadingReport, setLoadingReport] = useState(false);
  const [loadingCharts, setLoadingCharts] = useState(false);
  const [message, setMessage] = useState<string>("");
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const projectId = activeProject?.project_id;
  const datasetId = dataset?.dataset_id || activeProject?.dataset_id || null;
  const analysisIsCurrent = currentAnalysis(analysisResult, dataset, selectedModel, selectedVariables, modelOptions);
  const currentAnalysisId = analysisIsCurrent ? analysisResult?.analysis_id || null : null;
  const analysisForReport = analysisIsCurrent ? analysisResult : null;
  const analysisId = currentAnalysisId;
  const chartMatchesCurrentContext = (chart: any, aid?: string | null, _did?: string | null) => currentChart(chart, aid, dataset);

  const currentCharts = useMemo(
    () => charts.filter((chart) => chartMatchesCurrentContext(chart, analysisId, datasetId)),
    [charts, analysisId, datasetId, dataset?.data_fingerprint]
  );

  const selectedCharts = useMemo(
    () => currentCharts.filter((chart) => selectedIds.includes(chart.chart_id)),
    [currentCharts, selectedIds]
  );

  const reportContextSignature = useMemo(() => {
    const variableSig = selectedVariables
      ? {
          dependent_variable: selectedVariables.dependent_variable || selectedVariables.dependent_variables || selectedVariables.target || null,
          independent_variables: selectedVariables.independent_variables || selectedVariables.independent_vars || selectedVariables.predictors || [],
          control_variables: selectedVariables.control_variables || selectedVariables.control_vars || [],
          variable_roles: selectedVariables.variable_roles || selectedVariables.roles || null,
          variable_types: selectedVariables.variable_types || selectedVariables.types || null,
        }
      : null;
    return shortHash(stableStringify({
      project_id: projectId || null,
      dataset_id: datasetId || null,
      data_fingerprint: dataset?.data_fingerprint,
      report_style: style,
      report_mode: reportMode,
      analysis_id: currentAnalysisId || null,
      model: analysisForReport?.model || analysisForReport?.model_name || analysisForReport?.method || null,
      task_type: analysisForReport?.task_type || analysisForReport?.problem_type || null,
      dependent_from_analysis: analysisForReport?.dependent_variable || null,
      variables: variableSig,
      chart_ids: selectedIds.filter((id) => currentCharts.some((chart) => chart.chart_id === id)).sort(),
    }));
  }, [projectId, datasetId, currentAnalysisId, analysisForReport, selectedVariables, selectedIds, currentCharts, dataset?.data_fingerprint, style, reportMode]);

  useEffect(() => {
    if (!projectId) return;
    let alive = true;
    Promise.all([listCharts(projectId), listReports(projectId)]).then(([chartList, reportList]) => {
      if (!alive) return;
      setCharts(chartList.items); saveProjectCharts(projectId, chartList.items);
      setSelectedIds(chartList.items.filter(ch => currentChart(ch, analysisId, dataset)).slice(0, 4).map(ch => ch.chart_id));
      setHistory(reportList.items);
      const current = reportList.items.find(r => r.artifact_status === 'current');
      if (current) {setReport(current); setContent(current.draft_content ?? current.content ?? '');}
    }).catch((e: any) => {if (alive) setMessage(e.message);});
    return () => {alive = false;};
  }, [projectId, analysisId, dataset?.data_fingerprint]);

  useEffect(() => {
    if (!report?.report_id || content === (report.draft_content ?? report.content)) return;
    const timer = setTimeout(() => saveReportDraft(report.report_id, content).catch((e: any) => setMessage('服务器草稿保存失败：' + e.message)), 900);
    return () => clearTimeout(timer);
  }, [report?.report_id, content]);

  async function exportReport(format: string) {
    if (!report?.report_id) {downloadText(markdownFileName(activeProject?.name), content); return;}
    try {
      await saveReportDraft(report.report_id, content);
      await downloadArtifact(`/reports/${report.report_id}/download?format=${format}`, `${report.report_id}.${format === 'markdown' ? 'md' : format}`);
    } catch (e: any) {setMessage(e.message || '报告下载失败');}
  }

  const hasAnyContext = Boolean(activeProject || dataset || analysisResult);

  useEffect(() => {
    if (!content) return;
    try {
      localStorage.setItem(reportStorageKey(projectId), JSON.stringify({
        report,
        content,
        context_signature: reportContextSignature,
        edited_at: new Date().toISOString(),
      }));
    } catch {
      // local draft only
    }
  }, [content, report, projectId, reportContextSignature]);

  useEffect(() => {
    const loaded = loadProjectCharts(projectId);
    const compatible = loaded.filter((chart) => chartMatchesCurrentContext(chart, analysisId, datasetId));
    setCharts(loaded);
    setSelectedIds((prev) => {
      const kept = prev.filter((id) => compatible.some((chart) => chart.chart_id === id));
      const next = kept.length > 0 ? kept : compatible.slice(0, 4).map((chart) => chart.chart_id);
      if (prev.length === next.length && prev.every((id, idx) => id === next[idx])) return prev;
      return next;
    });

    const key = reportJobKey(projectId, reportContextSignature);
    const liveJob = reportJobs.get(key);
    if (liveJob) {
      attachReportJob(liveJob);
      return;
    }

    try {
      const cached = localStorage.getItem(reportStorageKey(projectId));
      if (cached) {
        const parsed = JSON.parse(cached);
        if (parsed?.content && parsed?.context_signature === reportContextSignature) {
          setReport(parsed.report || null);
          setContent(parsed.content || "");
          return;
        }
      }
      const jobState = loadReportJobState(projectId, reportContextSignature);
      if (jobState?.status === "done" && jobState?.content) {
        setReport(jobState.report || null);
        setContent(jobState.content || "");
        return;
      }
      if (jobState?.status === "pending") {
        setMessage("请先查看页面上方的后台任务：刷新页面不会取消服务器任务，完成后可打开结果；失败或中断的任务可以重试。");
      }
      setReport(null);
      setContent("");
    } catch {
      setReport(null);
      setContent("");
    }
  }, [projectId, analysisId, datasetId, reportContextSignature]);

  async function refreshCharts() {
    const loaded = loadProjectCharts(projectId);
    setCharts(loaded);
    const staleCount = loaded.filter((chart) => !chartMatchesCurrentContext(chart, analysisId, datasetId)).length;
    setMessage(staleCount ? `已刷新当前项目图表库，并隐藏 ${staleCount} 张旧分析图表。` : "已刷新当前项目的图表库。");
  }

  function toggleChart(chartId: string) {
    setSelectedIds((prev) => (prev.includes(chartId) ? prev.filter((id) => id !== chartId) : [...prev, chartId]));
  }

  async function generateRecommendedCharts() {
    if (!analysisId && !datasetId) {
      setMessage("当前没有分析结果或数据集，无法生成推荐图表。");
      return;
    }
    setLoadingCharts(true);
    setMessage("");
    try {
      const recRes = await recommendCharts({ analysis_id: analysisId, dataset_id: datasetId, max_charts: 6 });
      const recs = recRes.recommendations || [];
      setRecommendations(recs);
      if (recs.length === 0) {
        setMessage((recRes.warnings || []).join("\n") || "当前没有可自动生成的推荐图表。");
        return;
      }
      let nextCharts = loadProjectCharts(projectId);
      const compatibleBefore = nextCharts.filter((chart) => chartMatchesCurrentContext(chart, analysisId, datasetId));
      const newlySelected: string[] = [];
      for (const rec of recs.slice(0, 4)) {
        const existing = compatibleBefore.find((chart) => chart.chart_type === rec.chart_type);
        if (existing) {
          newlySelected.push(existing.chart_id);
          continue;
        }
        const genRes = await generateChart({
          analysis_id: analysisId,
          dataset_id: datasetId,
          chart_type: rec.chart_type,
          options: { top_n: 15 },
        });
        nextCharts = mergeChart(nextCharts, genRes.chart);
        newlySelected.push(genRes.chart.chart_id);
      }
      saveProjectCharts(projectId, nextCharts);
      setCharts(nextCharts);
      setSelectedIds((prev) => Array.from(new Set([...newlySelected, ...prev])).filter((id) => nextCharts.some((chart) => chart.chart_id === id && chartMatchesCurrentContext(chart, analysisId, datasetId))));
      setMessage("已生成/载入推荐图表，并默认勾选用于报告。");
    } catch (err: any) {
      setMessage(err?.message || "推荐图表生成失败");
    } finally {
      setLoadingCharts(false);
    }
  }

  function saveDraft(reportObj: any, draftContent: string) {
    setReport(reportObj);
    setContent(draftContent);
    setPreviewMode("preview");
    try {
      localStorage.setItem(reportStorageKey(projectId), JSON.stringify({ report: reportObj, content: draftContent, context_signature: reportContextSignature }));
    } catch {
      // local draft only
    }
  }

  function attachReportJob(job: ReportJobRecord) {
    setLoadingReport(true);
    setMessage("报告正在生成中。可以切换到其他页面，回来后会继续显示结果。");
    job.promise.then((result) => {
      if (!mountedRef.current) return;
      if (job.signature !== reportContextSignature) return;
      saveDraft(result.report, result.content);
      setMessage(result.message);
    }).catch((err: any) => {
      if (!mountedRef.current) return;
      setMessage(`报告生成任务失败：${err?.message || err}`);
    }).finally(() => {
      if (mountedRef.current && job.signature === reportContextSignature) setLoadingReport(false);
    });
  }

  async function buildReport() {
    if (!hasAnyContext) return;
    const key = reportJobKey(projectId, reportContextSignature);
    const existing = reportJobs.get(key);
    if (existing) {
      attachReportJob(existing);
      return;
    }
    setLoadingReport(true);
    setMessage("报告正在生成中。可以切换到其他页面，回来后会继续显示结果。");
    const payload = {
      project_id: projectId || null,
      analysis_id: currentAnalysisId || (!analysisResult ? analysisId || null : null),
      dataset_id: datasetId || null,
      title: activeProject?.name || "MethodPal 分析报告",
      format: "markdown",
      style,
      report_mode: reportMode,
      include_charts: true,
      chart_ids: selectedCharts.map((chart) => chart.chart_id),
      charts: selectedCharts,
      context: {
        project_name: activeProject?.name,
        research_goal: activeProject?.research_goal,
        dataset_name: dataset?.name,
        dataset_summary: dataset?.profile,
        row_count: dataset?.full_row_count || dataset?.profile?.row_count,
        column_count: dataset?.profile?.column_count || dataset?.headers?.length,
        selected_variables: selectedVariables,
        analysis_result: analysisForReport,
        ai_explanation: aiExplanation,
        ai_explanation_text: aiExplanation?.text || aiExplanation?.content || "",
        report_context_signature: reportContextSignature,
        current_analysis_id: currentAnalysisId || null,
        artifact_status: "current",
      },
    };

    const snapshot = {
      projectName: activeProject?.name,
      datasetName: dataset?.name,
      analysisResult: analysisForReport,
      selectedVariables,
      selectedCharts,
      style,
    };
    saveReportJobState(projectId, reportContextSignature, {
      status: "pending",
      started_at: new Date().toISOString(),
      context_signature: reportContextSignature,
    });

    const promise: Promise<ReportJobResult> = generateReport(payload).then((res) => {
      const nextContent = res?.content || buildLocalReportDraft(snapshot);
      const sourceLabel = res?.source === "llm" ? "AI 学术报告已生成" : res?.source === "template_fallback" ? "AI 生成失败，已回退到规则模板" : "规则模板报告已生成";
      const warningText = Array.isArray(res?.warnings) && res.warnings.length ? `\n${res.warnings.join("\n")}` : "";
      const result = {
        report: res,
        content: nextContent,
        ok: true,
        message: `${sourceLabel}，已把勾选图表写入报告。${warningText}`,
      };
      setHistory(prev => [res, ...prev.filter(r => r.report_id !== res.report_id)]);
      try {
        localStorage.setItem(reportStorageKey(projectId), JSON.stringify({ report: res, content: nextContent, context_signature: reportContextSignature }));
      } catch {
        // local draft only
      }
      saveReportJobState(projectId, reportContextSignature, {
        status: "done",
        completed_at: new Date().toISOString(),
        context_signature: reportContextSignature,
        report: res,
        content: nextContent,
      });
      return result;
    }).catch((err: any) => {
      const fallback = buildLocalReportDraft(snapshot);
      const res = { source: "frontend_fallback", error: String(err?.message || err) };
      const result = {
        report: res,
        content: fallback,
        ok: false,
        message: `后端报告生成失败，已在前端生成本地草稿：${err?.message || err}`,
      };
      try {
        localStorage.setItem(reportStorageKey(projectId), JSON.stringify({ report: res, content: fallback, context_signature: reportContextSignature }));
      } catch {
        // local draft only
      }
      saveReportJobState(projectId, reportContextSignature, {
        status: "done",
        completed_at: new Date().toISOString(),
        context_signature: reportContextSignature,
        report: res,
        content: fallback,
      });
      return result;
    }).finally(() => {
      reportJobs.delete(key);
    });

    const record: ReportJobRecord = { promise, signature: reportContextSignature, started_at: new Date().toISOString() };
    reportJobs.set(key, record);
    attachReportJob(record);
  }

  if (!hasAnyContext) {
    return (
      <div className="p-6 max-w-4xl">
        <div className="bg-card rounded-2xl border border-border p-8 text-center">
          <div className="w-12 h-12 rounded-2xl bg-secondary mx-auto flex items-center justify-center mb-4">
            <FileText size={22} className="text-primary" />
          </div>
          <h3 className="text-foreground font-semibold mb-2">还没有可生成报告的项目</h3>
          <p className="text-sm text-muted-foreground">请先创建项目、上传数据并运行至少一个分析。生成图表后，可以在这里把图表插入报告草稿。</p>
        </div>
      </div>
    );
  }

  return (
    <div className="p-6 space-y-6 max-w-7xl">
      <div className="bg-card rounded-2xl border border-border p-5">
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="text-xs text-muted-foreground mb-1">{activeProject?.name || "当前项目"} / {dataset?.name || "当前数据集"}</p>
            <h2 className="text-xl font-semibold text-foreground">报告草稿</h2>
            <p className="text-sm text-muted-foreground mt-1">把当前分析结果和项目图表组合成可复制、可下载的 Markdown 报告草稿。</p>
            {analysisResult?.analysis_id && !analysisIsCurrent && (
              <p className="text-xs text-amber-700 mt-2">检测到变量/数据上下文变化，旧分析结果已标记为过期；报告将不会使用旧模型指标。</p>
            )}
          </div>
          <div className="flex gap-2">
            <button
              onClick={generateRecommendedCharts}
              disabled={loadingCharts || (!analysisId && !datasetId)}
              className="flex items-center gap-2 px-3 py-2 rounded-lg border border-border text-sm text-muted-foreground disabled:opacity-50"
            >
              {loadingCharts ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />}
              生成推荐图表
            </button>
            <button
              onClick={buildReport}
              disabled={loadingReport}
              className="flex items-center gap-2 px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm disabled:opacity-50"
            >
              {loadingReport ? <Loader2 size={14} className="animate-spin" /> : <FileText size={14} />}
              生成报告草稿
            </button>
          </div>
        </div>
      </div>

      {message && <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800 whitespace-pre-wrap">{message}</div>}

      <div className="grid lg:grid-cols-[360px_1fr] gap-6 items-start">
        <div className="space-y-4">
          <div className="bg-card rounded-2xl border border-border p-5 space-y-4">
            <div>
              <h3 className="text-sm font-semibold text-foreground">报告设置</h3>
              <p className="text-xs text-muted-foreground mt-1">支持规则模板和 AI 学术写作。AI 模式会先提取统计事实，再调用 LLM 写报告。</p>
            </div>

            <div className="space-y-2">
              <label className="text-xs text-muted-foreground">生成方式</label>
              <select
                value={reportMode}
                onChange={(e) => setReportMode(e.target.value as any)}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground"
              >
                <option value="ai">AI 学术报告（基于统计事实写作）</option>
                <option value="template">基础模板报告（不调用 LLM）</option>
              </select>
              <p className="text-[11px] text-muted-foreground leading-5">
                AI 模式不会让模型自由编数字；后端会先整理样本、变量、模型指标、图表和风险提示，再交给 LLM 写自然语言段落。失败时自动回退模板。
              </p>
            </div>

            <div className="space-y-2">
              <label className="text-xs text-muted-foreground">写作风格</label>
              <select
                value={style}
                onChange={(e) => setStyle(e.target.value as any)}
                className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground"
              >
                <option value="academic">学术报告</option>
                <option value="plain">通俗解释</option>
                <option value="concise">简洁摘要</option>
              </select>
            </div>
            <div className="rounded-xl bg-secondary/40 p-3 space-y-2 text-xs text-muted-foreground">
              <p className="font-medium text-foreground">当前分析</p>
              <p>生成方式：{reportMode === "ai" ? "AI 学术报告" : "基础模板报告"}</p>
              <p>模型：{analysisForReport?.model_name || analysisForReport?.method || (analysisResult?.analysis_id && !analysisIsCurrent ? "旧分析已过期" : "未运行模型")}</p>
              <p>{metricSummary(analysisForReport)}</p>
              {analysisResult?.analysis_id && !analysisIsCurrent && <p className="text-amber-700">当前变量/数据上下文已变化，旧分析不会写入新报告。请重新运行模型。</p>}
            </div>
          </div>

          <div className="bg-card rounded-2xl border border-border p-5 space-y-4">
            <div className="flex items-center justify-between gap-3">
              <div>
                <h3 className="text-sm font-semibold text-foreground">插入图表</h3>
                <p className="text-xs text-muted-foreground mt-1">勾选图表后，会写入报告的“图表结果”章节。</p>
              </div>
              <button onClick={refreshCharts} className="p-2 rounded-lg border border-border text-muted-foreground hover:text-foreground">
                <RefreshCw size={14} />
              </button>
            </div>

            {currentCharts.length === 0 ? (
              <div className="rounded-xl border border-dashed border-border p-4 text-center">
                <BarChart2 size={20} className="mx-auto text-muted-foreground mb-2" />
                <p className="text-sm text-muted-foreground">当前分析/数据集还没有可插入图表。</p>
                <button
                  onClick={generateRecommendedCharts}
                  disabled={loadingCharts || (!analysisId && !datasetId)}
                  className="mt-3 px-3 py-2 rounded-lg bg-secondary text-sm text-foreground disabled:opacity-50"
                >
                  自动生成推荐图表
                </button>
              </div>
            ) : (
              <div className="space-y-2 max-h-[420px] overflow-y-auto pr-1">
                {currentCharts.map((chart) => {
                  const checked = selectedIds.includes(chart.chart_id);
                  return (
                    <button
                      key={chart.chart_id}
                      onClick={() => toggleChart(chart.chart_id)}
                      className={`w-full text-left rounded-xl border p-3 transition ${checked ? "border-primary/50 bg-primary/5" : "border-border hover:bg-secondary/40"}`}
                    >
                      <div className="flex gap-2 items-start">
                        {checked ? <CheckSquare size={16} className="text-primary mt-0.5" /> : <Square size={16} className="text-muted-foreground mt-0.5" />}
                        <div className="min-w-0">
                          <p className="text-sm font-medium text-foreground truncate">{chartLabel(chart)}</p>
                          <p className="text-[11px] text-muted-foreground mt-1">{chart.chart_type}</p>
                        </div>
                      </div>
                    </button>
                  );
                })}
              </div>
            )}

            {recommendations.length > 0 && (
              <div className="rounded-xl bg-secondary/40 p-3 text-xs text-muted-foreground">
                最近推荐：{recommendations.slice(0, 3).map((rec) => rec.title || rec.chart_type).join("、")}
              </div>
            )}
          </div>
        </div>

        <div className="space-y-4 min-w-0">
          <div className="bg-card rounded-2xl border border-border p-5">
            <div className="flex items-center justify-between gap-3 mb-4">
              <div>
                <h3 className="text-sm font-semibold text-foreground">报告预览</h3>
                <p className="text-xs text-muted-foreground mt-1">已选 {selectedCharts.length} 张图表。Markdown 可直接复制到 Word/Overleaf/Notion 后继续编辑。</p>
              </div>
              <div className="flex flex-wrap gap-2 justify-end">
                <div className="flex rounded-lg border border-border overflow-hidden">
                  <button
                    onClick={() => setPreviewMode("preview")}
                    disabled={!content}
                    className={`px-3 py-2 text-xs disabled:opacity-50 ${previewMode === "preview" ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-secondary/40"}`}
                  >
                    渲染预览
                  </button>
                  <button
                    onClick={() => setPreviewMode("edit")}
                    disabled={!content}
                    className={`px-3 py-2 text-xs border-l border-border disabled:opacity-50 ${previewMode === "edit" ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-secondary/40"}`}
                  >
                    编辑 Markdown
                  </button>
                </div>
                <button
                  onClick={() => copyText(content).then(() => setMessage("已复制 Markdown 报告草稿。"))}
                  disabled={!content}
                  className="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-border text-sm text-muted-foreground disabled:opacity-50"
                >
                  <Copy size={14} /> 复制
                </button>
                <button
                  onClick={() => exportReport('markdown')}
                  disabled={!content}
                  className="flex items-center gap-1.5 px-3 py-2 rounded-lg border border-border text-sm text-muted-foreground disabled:opacity-50"
                >
                  <Download size={14} /> 下载 md
                </button>
                {(['docx', 'pdf'] as const).map(fmt => <button key={fmt} onClick={() => exportReport(fmt)} disabled={!report?.report_id || !content} className="px-3 py-2 rounded-lg border border-border text-sm disabled:opacity-50">下载 {fmt === 'docx' ? 'Word' : 'PDF'}</button>)}
              </div>
            </div>

            {history.length > 0 && <details className="border rounded-lg p-3 mb-3"><summary className="text-sm">服务器报告历史（跨设备保存）</summary>{history.map(item => <div key={item.report_id} className="flex flex-wrap gap-3 py-2 text-xs"><span>{item.created_at || item.report_id} · {item.artifact_status === 'current' ? '当前版本' : '历史版本'}</span><button onClick={() => {setReport(item); setContent(item.draft_content ?? item.content); setMessage(item.artifact_status === 'stale' ? '正在查看历史报告，不代表当前数据分析结果。' : '已恢复服务器报告');}}>打开</button><button onClick={() => downloadArtifact(`/reports/${item.report_id}/download?format=pdf`, `${item.report_id}.pdf`).catch((e: any) => setMessage(e.message))}>下载 PDF</button></div>)}</details>}
            {content ? (
              <div className="rounded-xl border border-border bg-background">
                <div className="flex items-center gap-2 px-4 py-2 border-b border-border text-xs text-muted-foreground">
                  <Clipboard size={13} /> {report?.report_id ? `报告 ID：${report.report_id}` : "本地草稿"}{report?.source ? ` · ${report.source === "llm" ? "AI 写作" : report.source === "template_fallback" ? "模板回退" : "模板"}` : ""}
                </div>
                {previewMode === "edit" ? (
                  <textarea
                    value={content}
                    onChange={(e) => setContent(e.target.value)}
                    className="w-full min-h-[560px] p-4 text-sm leading-6 bg-background text-foreground outline-none resize-y font-mono"
                    spellCheck={false}
                  />
                ) : (
                  <div className="p-5 overflow-auto max-h-[620px]">
                    <MarkdownView content={content} />
                  </div>
                )}
              </div>
            ) : (
              <div className="rounded-xl border border-dashed border-border p-8 text-center">
                <FileText size={28} className="mx-auto text-muted-foreground mb-3" />
                <p className="text-sm text-muted-foreground">还没有生成报告草稿。先勾选图表，然后点击“生成报告草稿”。</p>
              </div>
            )}
          </div>

          {selectedCharts.length > 0 && (
            <div className="space-y-4">
              <div className="flex items-center gap-2 text-sm font-medium text-foreground">
                <BarChart2 size={16} /> 已选图表预览
              </div>
              {selectedCharts.map((chart) => <ChartRenderer key={chart.chart_id} chart={chart} />)}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
