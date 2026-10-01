function stable(value: any): string {
  if (Array.isArray(value)) return '[' + value.map(stable).join(',') + ']';
  if (value && typeof value === 'object') return '{' + Object.keys(value).sort().map(k => JSON.stringify(k) + ':' + stable(value[k])).join(',') + '}';
  return JSON.stringify(value);
}
export function currentAnalysis(analysis: any, dataset: any, model: string | null, selection: any, options: any) {
  const snap = analysis?.source_snapshot;
  if (!snap || !dataset?.data_fingerprint || snap.data_fingerprint !== dataset.data_fingerprint || analysis.artifact_status === 'stale') return false;
  if (model && analysis.model !== model) return false;
  const cfg = snap.config || {};
  if (stable(cfg.options || {}) !== stable(options || {})) return false;
  for (const role of ['dependent_variable', 'independent_variables', 'control_variables', 'mediator_variables', 'moderator_variables', 'group_variable', 'variable_types']) {
    const empty = role === 'variable_types' ? {} : role.endsWith('variables') ? [] : null;
    if (stable(cfg[role] || empty) !== stable(selection?.[role] || empty)) return false;
  }
  return true;
}
export function currentChart(chart: any, analysisId: string | null | undefined, dataset: any) {
  const meta = chart?.meta || {};
  if (!dataset?.data_fingerprint || meta.data_fingerprint !== dataset.data_fingerprint || chart.artifact_status === 'stale' || meta.artifact_status === 'stale') return false;
  const aid = String(chart.analysis_id || meta.analysis_id || '');
  if (meta.scope === 'dataset' || aid.startsWith('dataset:')) return meta.dataset_id === dataset.dataset_id;
  return Boolean(analysisId) && aid === analysisId;
}
