from typing import Any, Dict, List

import pandas as pd
from fastapi import HTTPException
from scipy import stats

from app.schemas.analysis import AnalysisRunRequest, AnalysisResponse
from app.services.model_tools.shared import dataset_to_dataframe, ensure_columns, numeric_series, clean_series, is_numeric_column, type_hint, round_float, p_value, response


def run(req: AnalysisRunRequest) -> AnalysisResponse:
    df, dataset = dataset_to_dataframe(req.dataset_id)
    if not req.dependent_variable or not req.group_variable:
        raise HTTPException(status_code=400, detail="ANOVA 需要因变量和分组变量。")
    ensure_columns(df, [req.dependent_variable, req.group_variable], "变量")
    if not is_numeric_column(df[req.dependent_variable], type_hint(req, req.dependent_variable)):
        raise HTTPException(status_code=400, detail="ANOVA 要求因变量为连续型/数值型变量。")
    work = pd.DataFrame({"y": numeric_series(df[req.dependent_variable]), "g": clean_series(df[req.group_variable])}).dropna()
    groups = sorted(work["g"].astype(str).unique().tolist())
    if len(groups) < 2:
        raise HTTPException(status_code=400, detail="分组变量至少需要两个组。")
    samples = [work[work["g"].astype(str) == g]["y"] for g in groups]
    f, p = stats.f_oneway(*samples)
    rows: List[Dict[str, Any]] = []
    for g, s in zip(groups, samples):
        rows.append({"group": g, "n": int(len(s)), "mean": round_float(s.mean()), "std": round_float(s.std(ddof=1)) if len(s) > 1 else 0.0})
    return response(req, "单因素方差分析", "One-way ANOVA", len(work), stats={"f_statistic": round_float(f), "p_value": p_value(p), "group_count": len(groups)}, result_table=rows, diagnostics={"groups": groups}, warnings=["当前版本提供整体 F 检验；事后比较将在后续版本补充。"], summary="已完成单因素方差分析。")
