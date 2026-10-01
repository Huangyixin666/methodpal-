from typing import Any, Dict, List

from fastapi import HTTPException
from scipy import stats

from app.schemas.analysis import AnalysisRunRequest, AnalysisResponse
from app.services.model_tools.shared import dataset_to_dataframe, ensure_columns, numeric_series, clean_series, is_numeric_column, is_binary_column, type_hint, round_float, p_value, response


def run(req: AnalysisRunRequest) -> AnalysisResponse:
    df, dataset = dataset_to_dataframe(req.dataset_id)
    if not req.dependent_variable or not req.group_variable:
        raise HTTPException(status_code=400, detail="独立样本 t 检验需要因变量和二分类分组变量。")
    ensure_columns(df, [req.dependent_variable, req.group_variable], "变量")
    if not is_numeric_column(df[req.dependent_variable], type_hint(req, req.dependent_variable)):
        raise HTTPException(status_code=400, detail="t 检验要求因变量为连续型/数值型变量。")
    if not is_binary_column(df[req.group_variable], type_hint(req, req.group_variable)):
        raise HTTPException(status_code=400, detail="t 检验要求分组变量为二分类。")
    y = numeric_series(df[req.dependent_variable])
    g = clean_series(df[req.group_variable])
    work = {"y": y, "g": g}
    import pandas as pd
    model_df = pd.DataFrame(work).dropna()
    groups = sorted(model_df["g"].astype(str).unique().tolist())
    if len(groups) != 2:
        raise HTTPException(status_code=400, detail="分组变量必须只有两个有效取值。")
    a = model_df[model_df["g"].astype(str) == groups[0]]["y"]
    b = model_df[model_df["g"].astype(str) == groups[1]]["y"]
    t, p = stats.ttest_ind(a, b, equal_var=False, nan_policy="omit")
    rows: List[Dict[str, Any]] = [
        {"group": groups[0], "n": int(len(a)), "mean": round_float(a.mean()), "std": round_float(a.std(ddof=1)) if len(a) > 1 else 0.0},
        {"group": groups[1], "n": int(len(b)), "mean": round_float(b.mean()), "std": round_float(b.std(ddof=1)) if len(b) > 1 else 0.0},
    ]
    stats_out = {"t_statistic": round_float(t), "p_value": p_value(p), "mean_difference": round_float(a.mean() - b.mean())}
    return response(req, "独立样本 t 检验", "Welch t-test", len(model_df), stats=stats_out, result_table=rows, diagnostics={"groups": groups}, warnings=["默认使用 Welch t 检验，不假设两组方差相等。"], summary="已完成两组均值差异检验。")
