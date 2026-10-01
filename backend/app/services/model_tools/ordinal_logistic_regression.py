from typing import Any, Dict, List

import pandas as pd
from fastapi import HTTPException
from statsmodels.miscmodels.ordinal_model import OrderedModel

from app.schemas.analysis import AnalysisRunRequest, AnalysisResponse, RegressionRow
from app.services.model_tools.shared import dataset_to_dataframe, ensure_columns, clean_series, numeric_series, is_numeric_column, type_hint, build_design_matrix, common_warnings, round_float, p_value, diagnostics_with_vif, response


def _ordinal_y(series: pd.Series) -> pd.Series:
    raw = clean_series(series)
    non_missing = raw.dropna().astype(str)
    if len(non_missing) == 0:
        return pd.Series(index=series.index, dtype="float64")
    # Prefer numeric order if all categories are numeric; otherwise use lexical order as MVP fallback.
    numeric = numeric_series(non_missing)
    if numeric.notna().all():
        values = sorted(numeric.unique().tolist())
        mapping = {str(int(v)) if float(v).is_integer() else str(v): i for i, v in enumerate(values)}
    else:
        values = sorted(non_missing.unique().tolist())
        mapping = {v: i for i, v in enumerate(values)}
    return raw.astype(str).map(mapping).astype("float64")


def run(req: AnalysisRunRequest) -> AnalysisResponse:
    df, dataset = dataset_to_dataframe(req.dataset_id)
    if not req.dependent_variable:
        raise HTTPException(status_code=400, detail="有序 Logistic 回归需要选择有序因变量。")
    x_cols = list(dict.fromkeys((req.independent_variables or []) + (req.control_variables or [])))
    if not x_cols:
        raise HTTPException(status_code=400, detail="有序 Logistic 回归至少需要一个自变量。")
    ensure_columns(df, [req.dependent_variable] + x_cols, "有序 Logistic 变量")
    y_ord = _ordinal_y(df[req.dependent_variable])
    if y_ord.dropna().nunique() < 3:
        raise HTTPException(status_code=400, detail="有序 Logistic 回归要求因变量至少有 3 个有序等级。")
    # Build X using a temporary numeric y column to reuse cleaning/encoding.
    tmp_col = "__ordinal_y__"
    work = df.copy()
    work[tmp_col] = y_ord.astype("object")
    temp_req = req.model_copy(update={"dependent_variable": tmp_col})
    y, X, meta, build_warnings = build_design_matrix(work, tmp_col, x_cols, temp_req, binary=False)
    try:
        model = OrderedModel(y.astype(int), X, distr="logit")
        fit = model.fit(method="bfgs", disp=False, maxiter=200)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="有序 Logistic 回归失败：{}".format(exc))
    rows: List[RegressionRow] = []
    for term, coef in fit.params.items():
        rows.append(RegressionRow(variable=str(term), coefficient=round_float(coef), std_error=round_float(fit.bse.get(term)), z_value=round_float(fit.tvalues.get(term)), p_value=p_value(fit.pvalues.get(term)), variable_type="threshold" if "/" in str(term) else "predictor", original_variable=str(term).split("_")[0]))
    stats_out = {"log_likelihood": round_float(fit.llf), "aic": round_float(fit.aic), "bic": round_float(fit.bic)}
    warnings = build_warnings + common_warnings(dataset, int(meta["n_rows_used"]), int(X.shape[1])) + ["有序等级的方向会影响解释，请确认变量编码顺序与研究含义一致。"]
    diagnostics = {"n_rows_original": int(meta["n_rows_original"]), "n_rows_used": int(meta["n_rows_used"]), "n_rows_dropped": int(meta["n_rows_dropped"]), "predictor_count": int(X.shape[1]), "design_matrix_columns": X.columns.tolist()}
    diagnostics, vif_warnings_list = diagnostics_with_vif(diagnostics, X)
    warnings.extend(vif_warnings_list)
    return response(req, "有序 Logistic 回归", "Ordered logit", int(meta["n_rows_used"]), stats=stats_out, regression_table=rows, diagnostics=diagnostics, warnings=warnings, summary="已完成有序 Logistic 回归。")
