from typing import Any, Dict

import math
import statsmodels.api as sm
from fastapi import HTTPException

from app.schemas.analysis import AnalysisRunRequest, AnalysisResponse
from app.services.model_tools.shared import dataset_to_dataframe, ensure_columns, numeric_series, is_numeric_column, type_hint, build_design_matrix, common_warnings, regression_rows_from_statsmodels, round_float, p_value, diagnostics_with_vif, response


def run(req: AnalysisRunRequest) -> AnalysisResponse:
    df, dataset = dataset_to_dataframe(req.dataset_id)
    if not req.dependent_variable:
        raise HTTPException(status_code=400, detail="Poisson 回归需要选择计数型因变量。")
    x_cols = list(dict.fromkeys((req.independent_variables or []) + (req.control_variables or [])))
    if not x_cols:
        raise HTTPException(status_code=400, detail="Poisson 回归至少需要一个自变量。")
    ensure_columns(df, [req.dependent_variable] + x_cols, "Poisson 变量")
    y_raw = numeric_series(df[req.dependent_variable])
    valid = y_raw.dropna()
    if len(valid) == 0 or (valid < 0).any():
        raise HTTPException(status_code=400, detail="Poisson 回归要求因变量为非负计数。")
    if not all(float(v).is_integer() for v in valid.tolist()):
        raise HTTPException(status_code=400, detail="Poisson 回归要求因变量为整数计数。")
    y, X, meta, build_warnings = build_design_matrix(df, req.dependent_variable, x_cols, req, binary=False)
    X_const = sm.add_constant(X, has_constant="add")
    try:
        model = sm.GLM(y, X_const, family=sm.families.Poisson()).fit()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Poisson 回归失败：{}".format(exc))
    rows = regression_rows_from_statsmodels(model, X, y)
    for row in rows:
        if row.variable != "截距" and row.coefficient is not None:
            row.odds_ratio = round_float(math.exp(row.coefficient))  # IRR shown in same field for compatibility
    stats_out = {"deviance": round_float(model.deviance), "pearson_chi2": round_float(model.pearson_chi2), "aic": round_float(model.aic), "bic": round_float(getattr(model, "bic", None)), "log_likelihood": round_float(model.llf)}
    warnings = build_warnings + common_warnings(dataset, int(meta["n_rows_used"]), int(X.shape[1])) + ["Poisson 回归的指数化系数可解释为 IRR；当前版本暂用 odds_ratio 字段承载。"]
    diagnostics = {"n_rows_original": int(meta["n_rows_original"]), "n_rows_used": int(meta["n_rows_used"]), "n_rows_dropped": int(meta["n_rows_dropped"]), "predictor_count": int(X.shape[1]), "design_matrix_columns": X.columns.tolist()}
    diagnostics, vif_warnings_list = diagnostics_with_vif(diagnostics, X)
    warnings.extend(vif_warnings_list)
    return response(req, "Poisson 回归", "GLM Poisson", int(meta["n_rows_used"]), stats=stats_out, regression_table=rows, diagnostics=diagnostics, warnings=warnings, summary="已完成 Poisson 计数回归。")
