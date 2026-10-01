from typing import Any, Dict, List

import math
from fastapi import HTTPException
from scipy import stats

from app.schemas.analysis import AnalysisRunRequest, AnalysisResponse
from app.services.model_tools.shared import dataset_to_dataframe, ensure_columns, is_numeric_column, type_hint, build_design_matrix, round_float, p_value, response
from app.services.model_tools.linear_regression import run as run_linear


def _coef_table_dict(resp: AnalysisResponse) -> Dict[str, Any]:
    return {row.variable: row.model_dump() for row in resp.regression_table}


def run(req: AnalysisRunRequest) -> AnalysisResponse:
    df, dataset = dataset_to_dataframe(req.dataset_id)
    if not req.dependent_variable or not req.independent_variables or not req.mediator_variables:
        raise HTTPException(status_code=400, detail="中介效应分析需要设置 Y、至少一个 X 和至少一个中介变量 M。")
    x = req.independent_variables[0]
    m = req.mediator_variables[0]
    y = req.dependent_variable
    controls = req.control_variables or []
    ensure_columns(df, [x, m, y] + controls, "中介模型变量")
    for col, label in [(y, "因变量 Y"), (m, "中介变量 M")]:
        if not is_numeric_column(df[col], type_hint(req, col)):
            raise HTTPException(status_code=400, detail="{} 需要是连续型/数值型。".format(label))
    # X may be numeric or categorical; build_design_matrix handles encoding. For path extraction, first encoded term containing x is used.
    a_req = req.model_copy(update={"model": "linear_regression", "dependent_variable": m, "independent_variables": [x], "control_variables": controls})
    b_req = req.model_copy(update={"model": "linear_regression", "dependent_variable": y, "independent_variables": [x, m], "control_variables": controls})
    c_req = req.model_copy(update={"model": "linear_regression", "dependent_variable": y, "independent_variables": [x], "control_variables": controls})
    a_resp = run_linear(a_req)
    b_resp = run_linear(b_req)
    c_resp = run_linear(c_req)

    def find_path(resp: AnalysisResponse, var: str) -> Dict[str, Any]:
        rows = [r.model_dump() for r in resp.regression_table if r.variable != "截距"]
        exact = [r for r in rows if r["variable"] == var]
        if exact:
            return exact[0]
        fuzzy = [r for r in rows if str(r.get("variable", "")).startswith(var)]
        if fuzzy:
            return fuzzy[0]
        raise HTTPException(status_code=400, detail="无法从模型结果中定位路径变量：{}".format(var))

    a = find_path(a_resp, x)
    b = find_path(b_resp, m)
    c_prime = find_path(b_resp, x)
    c_total = find_path(c_resp, x)
    a_coef = a.get("coefficient") or 0.0
    b_coef = b.get("coefficient") or 0.0
    indirect = float(a_coef) * float(b_coef)
    se_a = a.get("std_error") or 0.0
    se_b = b.get("std_error") or 0.0
    sobel_se = math.sqrt((float(b_coef) ** 2) * (float(se_a) ** 2) + (float(a_coef) ** 2) * (float(se_b) ** 2)) if se_a and se_b else None
    z = indirect / sobel_se if sobel_se and sobel_se > 0 else None
    sobel_p = 2 * (1 - stats.norm.cdf(abs(z))) if z is not None else None
    result_table: List[Dict[str, Any]] = [
        {"path": "a: X → M", "variable": x, **a},
        {"path": "b: M → Y | X", "variable": m, **b},
        {"path": "c′: X → Y | M", "variable": x, **c_prime},
        {"path": "c: X → Y", "variable": x, **c_total},
        {"path": "间接效应 a×b", "coefficient": round_float(indirect), "z_value": round_float(z), "p_value": p_value(sobel_p)},
    ]
    stats_out = {"a": round_float(a_coef), "b": round_float(b_coef), "c_prime": c_prime.get("coefficient"), "c_total": c_total.get("coefficient"), "indirect_effect": round_float(indirect), "sobel_z": round_float(z), "sobel_p_value": p_value(sobel_p)}
    warnings = ["当前为经典回归路径 + Sobel 检验的 MVP 版本；正式论文更建议使用 bootstrap 置信区间。"]
    diagnostics = {
        "x": x,
        "mediator": m,
        "y": y,
        "controls": controls,
        "vif_by_path": {
            "a_path_X_to_M": a_resp.diagnostics.get("vif", []),
            "b_cprime_path_X_M_to_Y": b_resp.diagnostics.get("vif", []),
            "c_path_X_to_Y": c_resp.diagnostics.get("vif", []),
        },
    }
    return response(req, "中介效应分析", "Regression mediation + Sobel test", min(a_resp.sample_size, b_resp.sample_size, c_resp.sample_size), stats=stats_out, result_table=result_table, diagnostics=diagnostics, warnings=warnings, summary="已完成中介效应分析。")
