import { AlertCircle, BarChart2, CheckCircle2, ChevronRight, Download, FileText, Info } from "lucide-react";
import { useWorkspace } from "../context/WorkspaceContext";
import { currentAnalysis } from '../lib/artifactVersions';
import { PagedDataTable } from './PagedDataTable';

type Props = { onNavigate: (page: string) => void };

function fmt(value: any, digits = 4) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  if (typeof value === "number") return value.toFixed(digits).replace(/0+$/, "").replace(/\.$/, "");
  return String(value);
}

function pFmt(value: any) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value);
  if (n < 0.001) return "< .001";
  return n.toFixed(4).replace(/0+$/, "").replace(/\.$/, "");
}

function sigClass(p: any) {
  const n = Number(p);
  if (!Number.isFinite(n)) return "bg-muted text-muted-foreground";
  if (n < 0.001) return "bg-primary/10 text-primary";
  if (n < 0.05) return "bg-blue-50 text-blue-600";
  return "bg-muted text-muted-foreground";
}

function vifClass(status: any) {
  if (status === "正常") return "bg-emerald-50 text-emerald-700";
  if (status === "需要注意") return "bg-amber-50 text-amber-700";
  if (status === "严重风险") return "bg-red-50 text-red-700";
  return "bg-muted text-muted-foreground";
}

function firstAvailable(obj: any, keys: string[]) {
  for (const key of keys) if (obj?.[key] !== undefined && obj?.[key] !== null) return obj[key];
  return undefined;
}

function renderValue(value: any) {
  if (Array.isArray(value)) return value.join("、");
  if (value && typeof value === "object") return JSON.stringify(value);
  return fmt(value);
}

function stableStringify(value: any) {
  const normalize = (v: any): any => {
    if (Array.isArray(v)) return v.map(normalize);
    if (v && typeof v === "object") {
      return Object.keys(v)
        .sort()
        .reduce((acc: any, key) => {
          acc[key] = normalize(v[key]);
          return acc;
        }, {});
    }
    return v;
  };

  return JSON.stringify(normalize(value));
}

function isSameTable(a: any[], b: any[]) {
  if (!Array.isArray(a) || !Array.isArray(b)) return false;
  if (a.length === 0 || b.length === 0) return false;
  return stableStringify(a) === stableStringify(b);
}

function getResultTableTitle(method: string) {
  const low = method.toLowerCase();
  if (method.includes("中介") || method.includes("调节") || low.includes("mediation") || low.includes("moderation")) return "路径 / 效应结果";
  if (method.includes("方差") || low.includes("anova")) return "方差分析结果";
  if (method.includes("卡方") || low.includes("chi")) return "卡方检验结果";
  if (method.includes("t 检验") || method.includes("t检验") || low.includes("t-test")) return "t 检验结果";
  return "检验结果";
}

function metricFromPrediction(predictionMetrics: any, key: string) {
  const test = predictionMetrics?.test || {};
  const cv = predictionMetrics?.cross_validation || {};
  const train = predictionMetrics?.train || {};
  if (test[key] !== undefined && test[key] !== null) return test[key];
  const cvKey = key === "r2" ? "cv_r2_mean" : key === "accuracy" ? "cv_accuracy_mean" : key === "auc" ? "cv_roc_auc_mean" : key === "f1" ? "cv_f1_mean" : undefined;
  if (cvKey && cv[cvKey] !== undefined && cv[cvKey] !== null) return cv[cvKey];
  if (train[key] !== undefined && train[key] !== null) return train[key];
  return undefined;
}

function hasPredictionMetrics(predictionMetrics: any) {
  return predictionMetrics && typeof predictionMetrics === "object" && Object.keys(predictionMetrics).length > 0;
}

function tableEqualsAny(raw: any[], tables: any[][]) {
  return tables.some((t) => isSameTable(raw, t));
}

export function ResultsPage({ onNavigate }: Props) {
  const { activeProject, dataset, analysisResult, selectedModel, selectedVariables, modelOptions } = useWorkspace();
  const isCurrent = currentAnalysis(analysisResult, dataset, selectedModel, selectedVariables, modelOptions);

  if (!activeProject || !dataset || !analysisResult) {
    return (
      <div className="p-6 max-w-4xl">
        <div className="bg-card rounded-2xl border border-border p-8 text-center">
          <h3 className="text-foreground mb-2">还没有分析结果</h3>
          <p className="text-sm text-muted-foreground mb-4">请先上传数据、设置变量并运行一个真实模型。</p>
          <button onClick={() => onNavigate("models")} className="px-4 py-2 rounded-xl bg-primary text-primary-foreground text-sm">打开模型选择</button>
        </div>
      </div>
    );
  }

  const stats = analysisResult.stats || {};
  const predictionMetrics = analysisResult.prediction_metrics || {};
  const modelComparison = analysisResult.model_comparison || [];
  const featureImportance = analysisResult.feature_importance || [];
  const predictionTable = analysisResult.prediction_table || [];
  const table = analysisResult.regression_table || [];
  const rawResultTable = analysisResult.result_table || [];
  const diagnostics = analysisResult.diagnostics || {};
  const warnings: string[] = [...(analysisResult.warnings || []), ...(diagnostics.warnings || [])];
  const vifRows = Array.isArray(diagnostics.vif) ? diagnostics.vif : [];
  const vifByPath = diagnostics.vif_by_path && typeof diagnostics.vif_by_path === "object" ? diagnostics.vif_by_path : {};
  const correlation = analysisResult.correlation_table || analysisResult.correlation_matrix || [];
  const descriptive = analysisResult.descriptive_table || analysisResult.descriptive_statistics || [];
  const method = analysisResult.model_name || analysisResult.method || "统计分析";
  const resultTable =
    tableEqualsAny(rawResultTable, [descriptive, correlation, table, modelComparison, featureImportance])
      ? []
      : rawResultTable;
  const resultTableTitle = getResultTableTitle(method);

  const mainP = firstAvailable(stats, ["p_value", "llr_p_value", "sobel_p_value"]);
  const mainStatistic =
    stats.f_statistic !== undefined ? `F = ${fmt(stats.f_statistic, 3)}` :
    stats.t_statistic !== undefined ? `t = ${fmt(stats.t_statistic, 3)}` :
    stats.chi_square !== undefined ? `χ² = ${fmt(stats.chi_square, 3)}` :
    stats.a_path !== undefined ? `ab = ${fmt(stats.indirect_effect_ab, 4)}` :
    stats.best_model !== undefined ? `最佳：${stats.best_model}` :
    hasPredictionMetrics(predictionMetrics) ? "预测评估" :
    "整体检验";

  const isClassificationEval = predictionMetrics?.task_type === "classification";
  const predPrimary = isClassificationEval ? metricFromPrediction(predictionMetrics, "accuracy") : metricFromPrediction(predictionMetrics, "r2");
  const predSecondary = isClassificationEval ? metricFromPrediction(predictionMetrics, "auc") : metricFromPrediction(predictionMetrics, "rmse");

  const metricCards = [
    { label: "样本量", value: diagnostics.n_rows_used ?? analysisResult.sample_size ?? stats.nobs ?? "—", sub: `原始 ${diagnostics.n_rows_original ?? dataset.profile.row_count} 行`, tone: "text-primary bg-secondary" },
    { label: hasPredictionMetrics(predictionMetrics) ? (isClassificationEval ? "预测 Accuracy" : "预测 R²") : (stats.pseudo_r2 !== undefined ? "Pseudo R²" : "R²"), value: hasPredictionMetrics(predictionMetrics) ? fmt(predPrimary, 4) : stats.r2 !== undefined ? fmt(stats.r2, 4) : stats.pseudo_r2 !== undefined ? fmt(stats.pseudo_r2, 4) : "—", sub: hasPredictionMetrics(predictionMetrics) ? (predictionMetrics.evaluation_protocol || "预测评估") : "模型解释力", tone: "text-emerald-600 bg-emerald-50" },
    { label: hasPredictionMetrics(predictionMetrics) ? (isClassificationEval ? "AUC" : "RMSE") : "主统计量", value: hasPredictionMetrics(predictionMetrics) ? fmt(predSecondary, 4) : mainStatistic, sub: hasPredictionMetrics(predictionMetrics) ? "泛化表现参考" : (analysisResult.method || "统计方法"), tone: "text-violet-600 bg-violet-50" },
    { label: hasPredictionMetrics(predictionMetrics) ? "评估协议" : "p 值", value: hasPredictionMetrics(predictionMetrics) ? (predictionMetrics.evaluation_protocol || "—") : (mainP !== undefined ? pFmt(mainP) : "—"), sub: hasPredictionMetrics(predictionMetrics) ? "train/test/cv" : "显著性参考", tone: "text-amber-600 bg-amber-50" },
  ];

  return (
    <div className="p-6 space-y-6 max-w-6xl">
      {!isCurrent && <div className="border border-amber-300 bg-amber-50 rounded-xl p-4 text-sm">这是历史分析结果，尚未证明与当前数据、变量和模型一致。请重新运行分析后再生成当前图表或报告。<button className="ml-3 text-primary" onClick={() => onNavigate('upload')}>去重新分析</button></div>}
      <div className="bg-card rounded-2xl border border-border p-5">
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="text-xs text-muted-foreground mb-1">{activeProject.name} / {dataset.name}</p>
            <h2 className="text-xl font-semibold text-foreground">{method}结果</h2>
            <p className="text-sm text-muted-foreground mt-1">{analysisResult.summary || "已基于当前上传数据完成真实后端统计分析。"}</p>
          </div>
          <div className="flex gap-2">
            <button onClick={() => onNavigate("charts")} className="flex items-center gap-2 px-3 py-2 rounded-lg border border-border text-sm text-muted-foreground">
              <BarChart2 size={14} />生成图表
            </button>
            <button className="flex items-center gap-2 px-3 py-2 rounded-lg border border-border text-sm text-muted-foreground">
              <Download size={14} />导出
            </button>
            <button onClick={() => onNavigate("explain")} className="flex items-center gap-2 px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm">
              AI 解释 <ChevronRight size={14} />
            </button>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {metricCards.map((card) => (
          <div key={card.label} className="bg-card rounded-xl p-4 border border-border">
            <div className={`w-8 h-8 rounded-lg flex items-center justify-center mb-2 ${card.tone}`}><FileText size={16} /></div>
            <p className="text-2xl font-semibold text-foreground">{card.value}</p>
            <p className="text-xs text-muted-foreground mt-0.5">{card.label} · {card.sub}</p>
          </div>
        ))}
      </div>

      {warnings.length > 0 && (
        <div className="bg-amber-50 border border-amber-200 rounded-xl p-4 flex gap-3">
          <AlertCircle size={18} className="text-amber-600 shrink-0 mt-0.5" />
          <div>
            <p className="text-sm font-medium text-amber-800 mb-1">结果解释提醒</p>
            <ul className="text-xs text-amber-700 space-y-1 list-disc pl-4">{warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
          </div>
        </div>
      )}


      {hasPredictionMetrics(predictionMetrics) && (
        <div className="bg-card rounded-2xl border border-border overflow-hidden">
          <div className="p-4 border-b border-border flex items-center justify-between">
            <div>
              <h3 className="text-foreground">预测评估</h3>
              <p className="text-xs text-muted-foreground mt-1">机器学习模型优先看测试集和交叉验证表现；训练集表现主要用于判断是否过拟合。</p>
            </div>
            <span className="text-xs text-muted-foreground">{predictionMetrics.evaluation_protocol || "train/test/cv"}</span>
          </div>
          <div className="grid md:grid-cols-3 gap-3 p-4">
            {[
              ["训练集", predictionMetrics.train || {}],
              ["测试集", predictionMetrics.test || {}],
              ["交叉验证", predictionMetrics.cross_validation || {}],
            ].map(([label, obj]: any) => (
              <div key={label} className="rounded-xl border border-border p-4 bg-muted/20">
                <p className="text-sm font-medium text-foreground mb-2">{label}</p>
                <div className="grid grid-cols-2 gap-2 text-xs">
                  {Object.keys(obj || {}).length === 0 ? <span className="text-muted-foreground col-span-2">暂无</span> : Object.entries(obj).map(([key, value]: any) => (
                    <div key={key} className="flex justify-between gap-2"><span className="text-muted-foreground">{key}</span><span className="font-mono text-foreground">{fmt(value)}</span></div>
                  ))}
                </div>
              </div>
            ))}
          </div>
          {Array.isArray(predictionMetrics.confusion_matrix) && predictionMetrics.confusion_matrix.length > 0 && (
            <div className="px-4 pb-4">
              <div className="rounded-xl border border-border overflow-hidden">
                <div className="px-3 py-2 bg-muted/40 text-xs font-medium text-muted-foreground">混淆矩阵</div>
                <table className="w-full text-sm"><tbody>{predictionMetrics.confusion_matrix.map((row: any, i: number) => <tr key={i}>{row.map((v: any, j: number) => <td key={j} className="px-3 py-2 border-t border-border font-mono">{fmt(v)}</td>)}</tr>)}</tbody></table>
              </div>
            </div>
          )}
        </div>
      )}

      {modelComparison.length > 0 && (
        <div className="bg-card rounded-2xl border border-border overflow-hidden">
          <div className="p-4 border-b border-border"><h3 className="text-foreground">模型对比</h3><p className="text-xs text-muted-foreground mt-1">用于比较预测性能，不等同于显著性或因果解释。</p></div>
          <div className="overflow-x-auto"><table className="w-full text-sm"><thead><tr className="bg-muted/50 text-xs text-muted-foreground text-left">{Object.keys(modelComparison[0] || {}).map((key) => <th key={key} className="py-2.5 px-3 font-medium">{key}</th>)}</tr></thead><tbody className="divide-y divide-border">{modelComparison.map((row: any, i: number) => <tr key={i}>{Object.keys(modelComparison[0] || {}).map((key) => <td key={key} className="py-2.5 px-3 font-mono text-muted-foreground">{renderValue(row[key])}</td>)}</tr>)}</tbody></table></div>
        </div>
      )}

      {featureImportance.length > 0 && (
        <div className="bg-card rounded-2xl border border-border overflow-hidden">
          <div className="p-4 border-b border-border"><h3 className="text-foreground">变量重要性 / 预测贡献</h3><p className="text-xs text-muted-foreground mt-1">重要性反映预测贡献，不代表统计显著或因果效应。</p></div>
          <div className="overflow-x-auto"><table className="w-full text-sm"><thead><tr className="bg-muted/50 text-xs text-muted-foreground text-left">{Object.keys(featureImportance[0] || {}).map((key) => <th key={key} className="py-2.5 px-3 font-medium">{key}</th>)}</tr></thead><tbody className="divide-y divide-border">{featureImportance.map((row: any, i: number) => <tr key={i}>{Object.keys(featureImportance[0] || {}).map((key) => <td key={key} className="py-2.5 px-3 font-mono text-muted-foreground">{renderValue(row[key])}</td>)}</tr>)}</tbody></table></div>
        </div>
      )}

      {(vifRows.length > 0 || Object.keys(vifByPath).length > 0) && (
        <div className="bg-card rounded-2xl border border-border overflow-hidden">
          <div className="p-4 border-b border-border flex items-center justify-between">
            <div>
              <h3 className="text-foreground">多重共线性诊断 VIF</h3>
              <p className="text-xs text-muted-foreground mt-1">VIF &lt; 5 通常可接受；5–10 需要注意；≥10 表示较强共线性风险。</p>
            </div>
            <span className="text-xs text-muted-foreground">基于后端编码后的设计矩阵计算</span>
          </div>

          {vifRows.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-muted/50 text-xs text-muted-foreground text-left">
                    <th className="py-2.5 px-3 font-medium">变量</th>
                    <th className="py-2.5 px-3 font-medium">VIF</th>
                    <th className="py-2.5 px-3 font-medium">判断</th>
                    <th className="py-2.5 px-3 font-medium">解释</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {vifRows.map((row: any, i: number) => (
                    <tr key={i} className="hover:bg-muted/30">
                      <td className="py-2.5 px-3 font-medium text-foreground">{row.variable}</td>
                      <td className="py-2.5 px-3 font-mono text-foreground">{fmt(row.vif)}</td>
                      <td className="py-2.5 px-3"><span className={`px-2 py-0.5 rounded text-xs ${vifClass(row.status)}`}>{row.status || "—"}</span></td>
                      <td className="py-2.5 px-3 text-xs text-muted-foreground">{row.interpretation || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {Object.keys(vifByPath).length > 0 && (
            <div className="p-4 space-y-4">
              {Object.entries(vifByPath).map(([pathName, rows]: any) => (
                <div key={pathName} className="rounded-xl border border-border overflow-hidden">
                  <div className="px-3 py-2 bg-muted/40 text-xs font-medium text-muted-foreground">{pathName}</div>
                  {Array.isArray(rows) && rows.length > 0 ? (
                    <div className="overflow-x-auto">
                      <table className="w-full text-xs">
                        <thead><tr className="text-left text-muted-foreground"><th className="py-2 px-3">变量</th><th className="py-2 px-3">VIF</th><th className="py-2 px-3">判断</th><th className="py-2 px-3">解释</th></tr></thead>
                        <tbody className="divide-y divide-border">
                          {rows.map((row: any, i: number) => (
                            <tr key={i}>
                              <td className="py-2 px-3 font-medium text-foreground">{row.variable}</td>
                              <td className="py-2 px-3 font-mono">{fmt(row.vif)}</td>
                              <td className="py-2 px-3"><span className={`px-2 py-0.5 rounded ${vifClass(row.status)}`}>{row.status || "—"}</span></td>
                              <td className="py-2 px-3 text-muted-foreground">{row.interpretation || "—"}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <div className="px-3 py-3 text-xs text-muted-foreground">该路径模型没有可显示的 VIF 结果。</div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {table.length > 0 && (
        <div className="bg-card rounded-2xl border border-border overflow-hidden">
          <div className="p-4 border-b border-border flex items-center justify-between"><h3 className="text-foreground">模型系数表</h3><span className="text-xs text-muted-foreground">系数来自 Python 后端真实计算</span></div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr className="bg-muted/50 text-xs text-muted-foreground text-left"><th className="py-2.5 px-3 font-medium">变量</th><th className="py-2.5 px-3 font-medium">B</th><th className="py-2.5 px-3 font-medium">SE</th><th className="py-2.5 px-3 font-medium">t / z</th><th className="py-2.5 px-3 font-medium">p</th><th className="py-2.5 px-3 font-medium">95% CI</th><th className="py-2.5 px-3 font-medium">OR / β</th></tr></thead>
              <tbody className="divide-y divide-border">
                {table.map((row: any, idx: number) => (
                  <tr key={idx} className="hover:bg-muted/30">
                    <td className="py-2.5 px-3 font-medium text-foreground">{row.variable || row.term || row.name}</td>
                    <td className="py-2.5 px-3 font-mono text-foreground">{fmt(row.coefficient ?? row.beta)}</td>
                    <td className="py-2.5 px-3 font-mono text-muted-foreground">{fmt(row.std_error)}</td>
                    <td className="py-2.5 px-3 font-mono text-foreground">{fmt(row.t_value ?? row.z_value)}</td>
                    <td className="py-2.5 px-3"><span className={`px-2 py-0.5 rounded text-xs ${sigClass(row.p_value)}`}>{pFmt(row.p_value)}</span></td>
                    <td className="py-2.5 px-3 font-mono text-muted-foreground">[{fmt(row.ci_lower)}, {fmt(row.ci_upper)}]</td>
                    <td className="py-2.5 px-3 font-mono text-muted-foreground">{row.odds_ratio !== undefined && row.odds_ratio !== null ? `OR=${fmt(row.odds_ratio)}` : row.standardized_beta !== undefined && row.standardized_beta !== null ? `β=${fmt(row.standardized_beta)}` : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {resultTable.length > 0 && (
        <PagedDataTable key={`tests:${analysisResult.analysis_id}`} title={resultTableTitle} rows={resultTable} renderValue={renderValue} />
      )}

      {descriptive.length > 0 && (
        <div className="bg-card rounded-2xl border border-border overflow-hidden">
          <div className="p-4 border-b border-border"><h3 className="text-foreground">描述统计</h3></div>
          <div className="overflow-x-auto"><table className="w-full text-sm"><thead><tr className="bg-muted/50 text-xs text-muted-foreground text-left">{Object.keys(descriptive[0] || {}).map((key) => <th key={key} className="py-2.5 px-3 font-medium">{key}</th>)}</tr></thead><tbody className="divide-y divide-border">{descriptive.map((row: any, i: number) => <tr key={i}>{Object.keys(descriptive[0] || {}).map((key) => <td key={key} className="py-2.5 px-3 font-mono text-muted-foreground">{renderValue(row[key])}</td>)}</tr>)}</tbody></table></div>
        </div>
      )}

      {correlation.length > 0 && (
        <PagedDataTable key={`correlation:${analysisResult.analysis_id}`} title="相关分析结果" rows={correlation} renderValue={renderValue} fixedColumn="variable" />
      )}

      {predictionTable.length > 0 && (
        <div className="bg-card rounded-2xl border border-border overflow-hidden">
          <div className="p-4 border-b border-border"><h3 className="text-foreground">预测样本预览</h3><p className="text-xs text-muted-foreground mt-1">仅展示部分样本，用于检查预测值、真实值和残差。</p></div>
          <div className="overflow-x-auto"><table className="w-full text-sm"><thead><tr className="bg-muted/50 text-xs text-muted-foreground text-left">{Object.keys(predictionTable[0] || {}).map((key) => <th key={key} className="py-2.5 px-3 font-medium">{key}</th>)}</tr></thead><tbody className="divide-y divide-border">{predictionTable.map((row: any, i: number) => <tr key={i}>{Object.keys(predictionTable[0] || {}).map((key) => <td key={key} className="py-2.5 px-3 font-mono text-muted-foreground">{renderValue(row[key])}</td>)}</tr>)}</tbody></table></div>
        </div>
      )}

      <div className="grid md:grid-cols-2 gap-4">
        <div className="bg-card rounded-2xl border border-border p-5"><div className="flex items-center gap-2 mb-3"><CheckCircle2 size={16} className="text-emerald-500" /><h3 className="text-foreground">后端计算信息</h3></div><div className="space-y-2 text-xs text-muted-foreground"><p>原始行数：{diagnostics.n_rows_original ?? "—"}</p><p>实际使用行数：{diagnostics.n_rows_used ?? analysisResult.sample_size ?? "—"}</p><p>删除行数：{diagnostics.n_rows_dropped ?? "—"}</p><p>预测变量数：{diagnostics.predictor_count ?? "—"}</p></div></div>
        <div className="bg-card rounded-2xl border border-border p-5"><div className="flex items-center gap-2 mb-3"><Info size={16} className="text-primary" /><h3 className="text-foreground">下一步建议</h3></div><div className="space-y-2 text-xs text-muted-foreground"><p>1. 先确认缺失值和异常值是否已处理。</p><p>2. 检查变量类型是否符合模型要求。</p><p>3. 进入图表分析页生成特征重要性、混淆矩阵或模型对比图。</p></div></div>
      </div>

      <button onClick={() => onNavigate("explain")} className="flex items-center gap-2 text-sm bg-primary text-primary-foreground px-5 py-2.5 rounded-xl hover:opacity-90 transition-opacity">查看 AI 结果解释 <ChevronRight size={16} /></button>
    </div>
  );
}
