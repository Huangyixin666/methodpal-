import { getCurrentUserId } from "../lib/api";
import { listCharts, downloadArtifact } from '../lib/api';
import { currentAnalysis, currentChart } from '../lib/artifactVersions';
import { useEffect, useMemo, useState } from "react";
import { BarChart2, ChevronRight, Loader2, RefreshCw, Sparkles } from "lucide-react";
import { generateChart, recommendCharts } from "../lib/api";
import type { ChartRecommendation, ChartSpec } from "../lib/api";
import { useWorkspace } from "../context/WorkspaceContext";
import { ChartRenderer } from "./ChartRenderer";

type Props = { onNavigate: (page: string) => void };

function recommendationLabel(rec: ChartRecommendation) {
  return rec.title || rec.chart_type;
}

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
    localStorage.setItem(projectChartStorageKey(projectId), JSON.stringify((charts || []).slice(0, 50)));
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
  // 数据集级图表，例如相关矩阵，只要求数据集一致。
  if (aid.startsWith("dataset:") || did) return Boolean(datasetId && did === datasetId);
  // 模型级图表，例如特征重要性、混淆矩阵，必须匹配当前 analysis_id。
  if (analysisId) return !aid || aid === analysisId;
  return !aid;
}

function chartContextKey(chart: ChartSpec | any) {
  return `${chart?.chart_type || "chart"}:${chartAnalysisId(chart) || chartDatasetId(chart) || "no_context"}`;
}

function mergeChart(prev: ChartSpec[], chart: ChartSpec) {
  const key = chartContextKey(chart);
  return [chart, ...prev.filter((c) => c.chart_id !== chart.chart_id && chartContextKey(c) !== key)].slice(0, 50);
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

export function ChartsPage({ onNavigate }: Props) {
  const { activeProject, dataset, analysisResult, selectedVariables, selectedModel, modelOptions } = useWorkspace();
  const [recommendations, setRecommendations] = useState<ChartRecommendation[]>([]);
  const [charts, setCharts] = useState<ChartSpec[]>([]);
  const [loading, setLoading] = useState(false);
  const [generating, setGenerating] = useState<string | null>(null);
  const [message, setMessage] = useState<string>("");
  const [selectedChartId, setSelectedChartId] = useState<string | null>(null);

  const datasetId = dataset?.dataset_id || null;
  const analysisIsCurrent = currentAnalysis(analysisResult, dataset, selectedModel, selectedVariables, modelOptions);
  const analysisId = analysisIsCurrent ? analysisResult?.analysis_id || null : null;
  const projectId = activeProject?.project_id;
  const chartMatchesCurrentContext = (chart: any, aid?: string | null, _did?: string | null) => currentChart(chart, aid, dataset);

  const currentCharts = useMemo(
    () => charts.filter((chart) => chartMatchesCurrentContext(chart, analysisId, datasetId)),
    [charts, analysisId, datasetId, dataset?.data_fingerprint]
  );
  const staleCharts = useMemo(
    () => charts.filter((chart) => !chartMatchesCurrentContext(chart, analysisId, datasetId)),
    [charts, analysisId, datasetId, dataset?.data_fingerprint]
  );

  const selectedChart = useMemo(() => {
    if (currentCharts.length === 0) return null;
    return currentCharts.find((chart) => chart.chart_id === selectedChartId) || currentCharts[0];
  }, [currentCharts, selectedChartId]);

  const topSuggestions = useMemo(() => recommendations.slice(0, 6), [recommendations]);

  async function loadRecommendations(autoGenerate = false) {
    if (!analysisId && !datasetId) return;
    setLoading(true);
    setMessage("");
    try {
      const res = await recommendCharts({ analysis_id: analysisId, dataset_id: datasetId, max_charts: 8 });
      setRecommendations(res.recommendations || []);
      const baseMessage = (res.warnings || []).join("\n");
      if (baseMessage) setMessage(baseMessage);
      if (autoGenerate && (res.recommendations || []).length > 0 && currentCharts.length === 0) {
        const first = res.recommendations[0];
        await handleGenerate(first.chart_type, true);
      }
    } catch (err: any) {
      setMessage(err?.message || "图表推荐失败");
    } finally {
      setLoading(false);
    }
  }

  async function handleGenerate(chartType: string, silent = false) {
    if (!analysisId && !datasetId) return;
    setGenerating(chartType);
    if (!silent) setMessage("");
    try {
      const res = await generateChart({ analysis_id: analysisId, dataset_id: datasetId, chart_type: chartType, options: { top_n: 15 } });
      const chart = {
        ...res.chart,
        meta: {
          ...(res.chart.meta || {}),
          analysis_id: res.chart.analysis_id || analysisId || undefined,
          dataset_id: chartDatasetId(res.chart) || datasetId || undefined,
          artifact_status: "current",
          generated_at: new Date().toISOString(),
        },
      } as ChartSpec;
      setSelectedChartId(chart.chart_id);
      setCharts((prev) => {
        const next = mergeChart(prev, chart);
        saveProjectCharts(projectId, next);
        return next;
      });
      return true;
    } catch (err: any) {
      setMessage(err?.message || "图表生成失败");
      return false;
    } finally {
      setGenerating(null);
    }
  }

  async function generateAll() {
    let succeeded = 0;
    for (const rec of topSuggestions.slice(0, 4)) {
      if (await handleGenerate(rec.chart_type, true)) succeeded += 1;
    }
    setMessage(`生成完成：${succeeded} 张成功，${Math.min(topSuggestions.length, 4) - succeeded} 张失败。旧版本可在历史图表中下载。`);
  }

  async function refreshCharts() {
    const loaded = projectId ? (await listCharts(projectId)).items : loadProjectCharts(projectId);
    saveProjectCharts(projectId, loaded);
    setCharts(loaded);
    const fresh = loaded.filter((chart) => chartMatchesCurrentContext(chart, analysisId, datasetId));
    setSelectedChartId(fresh[0]?.chart_id || null);
    const staleCount = loaded.filter((chart) => !chartMatchesCurrentContext(chart, analysisId, datasetId)).length;
    setMessage(staleCount ? `已刷新图表库，并隐藏 ${staleCount} 张旧状态图表。` : "已刷新当前图表库。");
  }

  useEffect(() => {
    let alive = true;
    const loaded = loadProjectCharts(projectId);
    setCharts(loaded);
    const fresh = loaded.filter((chart) => chartMatchesCurrentContext(chart, analysisId, datasetId));
    setSelectedChartId(fresh[0]?.chart_id || null);
    setRecommendations([]);
    if (analysisId || datasetId) loadRecommendations(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
    if (projectId) listCharts(projectId).then(res => {if (alive) {setCharts(res.items); saveProjectCharts(projectId, res.items);}}).catch((e: any) => {if (alive) setMessage(e.message);});
    return () => {alive = false;};
  }, [analysisId, datasetId, projectId, dataset?.data_fingerprint]);


  useEffect(() => {
    if (currentCharts.length === 0) {
      if (selectedChartId) setSelectedChartId(null);
      return;
    }
    if (!selectedChartId || !currentCharts.some((chart) => chart.chart_id === selectedChartId)) {
      setSelectedChartId(currentCharts[0].chart_id);
    }
  }, [currentCharts, selectedChartId]);

  if (!activeProject || !dataset) {
    return (
      <div className="p-6 max-w-4xl">
        <div className="bg-card rounded-2xl border border-border p-8 text-center">
          <div className="w-12 h-12 rounded-2xl bg-secondary mx-auto flex items-center justify-center mb-4">
            <BarChart2 size={22} className="text-primary" />
          </div>
          <h3 className="text-foreground font-semibold mb-2">还没有可作图的数据</h3>
          <p className="text-sm text-muted-foreground mb-4">请先上传数据。上传后可以直接生成相关矩阵；运行模型后可生成变量重要性、混淆矩阵和模型对比图。</p>
          <button onClick={() => onNavigate("upload")} className="px-4 py-2 rounded-xl bg-primary text-primary-foreground text-sm">去数据上传</button>
        </div>
      </div>
    );
  }

  return (
    <div className="p-6 space-y-6 max-w-6xl">
      <div className="bg-card rounded-2xl border border-border p-5">
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="text-xs text-muted-foreground mb-1">{activeProject.name} / {dataset.name}</p>
            <h2 className="text-xl font-semibold text-foreground">结果图表</h2>
            <p className="text-sm text-muted-foreground mt-1">基于当前分析结果自动推荐图表。当前模型：{analysisResult?.model_name || analysisResult?.method || "未运行模型，可生成数据图"}</p>
            {analysisResult?.analysis_id && !analysisIsCurrent && (
              <p className="text-xs text-amber-700 mt-2">检测到当前变量/数据上下文已变化，旧分析结果已视为过期；本页只保留数据集级图表推荐。请重新运行模型后再生成模型级图表。</p>
            )}
            {staleCharts.length > 0 && (
              <p className="text-xs text-amber-700 mt-2">已隐藏 {staleCharts.length} 张旧状态图表。旧图表不会进入报告或 Agent 当前上下文。</p>
            )}
          </div>
          <div className="flex gap-2">
            <button onClick={refreshCharts} className="flex items-center gap-2 px-3 py-2 rounded-lg border border-border text-sm text-muted-foreground">
              <RefreshCw size={14} /> 刷新图表库
            </button>
            <button onClick={() => loadRecommendations(false)} className="flex items-center gap-2 px-3 py-2 rounded-lg border border-border text-sm text-muted-foreground">
              {loading ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />} 重新推荐
            </button>
            <button onClick={generateAll} disabled={topSuggestions.length === 0 || Boolean(generating)} className="flex items-center gap-2 px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm disabled:opacity-50">
              <Sparkles size={14} /> 生成推荐图表
            </button>
          </div>
        </div>
      </div>

      {message && (
        <div className="bg-amber-50 border border-amber-200 rounded-xl p-4 text-sm text-amber-800 whitespace-pre-wrap">{message}</div>
      )}

      <div className="bg-card rounded-2xl border border-border p-5">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h3 className="text-foreground font-medium">推荐图表</h3>
            <p className="text-xs text-muted-foreground mt-1">推荐来自当前数据集和当前 analysis_result。更改数据、变量或模型后，旧结果图表会自动隐藏。</p>
          </div>
          <button onClick={() => onNavigate("results")} className="text-xs text-muted-foreground hover:text-foreground flex items-center gap-1">返回分析结果 <ChevronRight size={13} /></button>
        </div>
        {topSuggestions.length === 0 ? (
          <p className="text-sm text-muted-foreground">暂无可推荐图表。可以先运行 XGBoost / CatBoost / 随机森林 / 模型对比 / 回归模型。</p>
        ) : (
          <div className="grid md:grid-cols-2 gap-3">
            {topSuggestions.map((rec) => (
              <button
                key={rec.chart_type}
                onClick={() => handleGenerate(rec.chart_type)}
                disabled={generating === rec.chart_type}
                className="text-left rounded-xl border border-border p-4 hover:border-primary/40 hover:bg-secondary/30 transition disabled:opacity-60"
              >
                <div className="flex items-center justify-between gap-2">
                  <p className="text-sm font-medium text-foreground">{recommendationLabel(rec)}</p>
                  {generating === rec.chart_type ? <Loader2 size={14} className="animate-spin text-primary" /> : <ChevronRight size={14} className="text-muted-foreground" />}
                </div>
                <p className="text-xs text-muted-foreground mt-1">{rec.description}</p>
                {rec.reason && <p className="text-[11px] text-muted-foreground mt-2">依据：{rec.reason}</p>}
              </button>
            ))}
          </div>
        )}
      </div>

      {currentCharts.length > 0 && selectedChart ? (
        <div className="space-y-4">
          <div className="bg-card rounded-2xl border border-border p-4">
            <div className="flex items-center justify-between gap-3 mb-3">
              <div>
                <h3 className="text-sm font-medium text-foreground">当前展示图表</h3>
                <p className="text-xs text-muted-foreground mt-1">点击上方推荐卡片生成哪张图，就只切换展示那张图；下方图表库可手动切换历史当前图。</p>
              </div>
              <span className="text-xs text-muted-foreground">当前上下文共 {currentCharts.length} 张图</span>
            </div>
            <div className="flex flex-wrap gap-2">
              {currentCharts.map((chart) => (
                <button
                  key={chart.chart_id}
                  onClick={() => setSelectedChartId(chart.chart_id)}
                  className={`px-3 py-1.5 rounded-lg border text-xs transition ${selectedChart.chart_id === chart.chart_id ? "border-primary bg-secondary text-primary" : "border-border text-muted-foreground hover:text-foreground"}`}
                  title={chart.chart_type}
                >
                  {chart.title || chart.chart_type}
                </button>
              ))}
            </div>
          </div>
          <div className="flex gap-2">{(['png', 'svg'] as const).map(fmt => <button key={fmt} className="border rounded px-3 py-2 text-xs" onClick={() => downloadArtifact(`/charts/${selectedChart.chart_id}/download?format=${fmt}`, `${selectedChart.chart_id}.${fmt}`).catch((e: any) => setMessage(e.message))}>下载 {fmt.toUpperCase()}</button>)}</div>
          <ChartRenderer key={selectedChart.chart_id} chart={selectedChart} />
        </div>
      ) : (
        <div className="bg-card rounded-2xl border border-border p-8 text-center">
          <p className="text-sm text-muted-foreground">当前上下文还没有生成图表。点击上方推荐卡片，或点击“生成推荐图表”。</p>
        </div>
      )}
      {staleCharts.length > 0 && <details className="border rounded-xl p-4"><summary className="text-sm">历史图表（{staleCharts.length} 张，不用于当前报告）</summary>{staleCharts.map(ch => <div key={ch.chart_id} className="flex gap-3 text-xs py-2"><span>{ch.title} · {ch.analysis_id}</span><button onClick={() => downloadArtifact(`/charts/${ch.chart_id}/download?format=png`, `${ch.chart_id}.png`).catch((e: any) => setMessage(e.message))}>下载历史 PNG</button></div>)}</details>}
    </div>
  );
}
