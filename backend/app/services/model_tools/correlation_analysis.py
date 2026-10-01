from typing import Any, Dict, List

import pandas as pd
from scipy import stats
from fastapi import HTTPException

from app.schemas.analysis import AnalysisRunRequest, AnalysisResponse
from app.services.model_tools.shared import dataset_to_dataframe, selected_columns, numeric_series, is_numeric_column, type_hint, round_float, p_value, response
from app.services.job_progress import progress

MAX_CORRELATION_COLUMNS = 100


def run(req: AnalysisRunRequest) -> AnalysisResponse:
    df, dataset = dataset_to_dataframe(req.dataset_id)
    columns = selected_columns(req, df)
    # Pairwise tests and browser matrix rendering grow quadratically. Never
    # silently truncate the scientific calculation to fit a shared server.
    if len(columns) > MAX_CORRELATION_COLUMNS:
        raise HTTPException(400, '相关分析最多选择 100 个字段，请先选择研究所需变量或分批分析；没有自动删减字段。')
    numeric_cols: List[str] = []
    numeric_df = pd.DataFrame()
    for col in columns:
        if is_numeric_column(df[col], type_hint(req, col)):
            numeric_cols.append(col)
            numeric_df[col] = numeric_series(df[col])
    if len(numeric_cols) < 2:
        raise HTTPException(status_code=400, detail="相关分析至少需要两个数值变量。")
    corr = numeric_df.corr(method="pearson", min_periods=3)
    progress('正在计算相关矩阵与逐对检验', 35)
    matrix: List[Dict[str, Any]] = []
    for r in corr.index:
        item: Dict[str, Any] = {"variable": r}
        for c in corr.columns:
            item[c] = round_float(corr.loc[r, c])
        matrix.append(item)
    pairs: List[Dict[str, Any]] = []
    warnings = []
    constant_columns = [c for c in numeric_cols if numeric_df[c].nunique(dropna=True) < 2]
    if constant_columns:
        warnings.append('以下字段没有足够变化，相关系数和 p 值无法估计：' + '、'.join(constant_columns))
    total_pairs = len(numeric_cols) * (len(numeric_cols) - 1) // 2
    checked_pairs = 0
    for i, a in enumerate(numeric_cols):
        for b in numeric_cols[i + 1:]:
            pair_df = numeric_df[[a, b]].dropna()
            if len(pair_df) >= 3 and pair_df[a].nunique() > 1 and pair_df[b].nunique() > 1:
                r, p = stats.pearsonr(pair_df[a], pair_df[b])
                pairs.append({"variable_1": a, "variable_2": b, "r": round_float(r), "p_value": p_value(p), "n": int(len(pair_df))})
            checked_pairs += 1
            if checked_pairs % 100 == 0 or checked_pairs == total_pairs:
                progress('相关检验：{} / {} 对变量'.format(checked_pairs, total_pairs), 35 + int(50 * checked_pairs / total_pairs))
    return response(req, "Pearson 相关分析", "Pearson correlation", len(numeric_df), correlation_matrix=matrix, result_table=pairs, warnings=warnings, diagnostics={"numeric_columns": numeric_cols, "pair_count": len(pairs)}, summary="已完成 {} 个数值变量的 Pearson 相关分析。".format(len(numeric_cols)))
