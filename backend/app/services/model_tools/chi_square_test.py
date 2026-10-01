from typing import Any, Dict, List

import pandas as pd
from fastapi import HTTPException
from scipy import stats

from app.schemas.analysis import AnalysisRunRequest, AnalysisResponse
from app.services.model_tools.shared import dataset_to_dataframe, ensure_columns, clean_series, p_value, round_float, response


def run(req: AnalysisRunRequest) -> AnalysisResponse:
    df, dataset = dataset_to_dataframe(req.dataset_id)
    y = req.dependent_variable
    g = req.group_variable or (req.independent_variables[0] if req.independent_variables else None)
    if not y or not g:
        raise HTTPException(status_code=400, detail="卡方检验需要两个分类变量。")
    ensure_columns(df, [y, g], "变量")
    work = pd.DataFrame({"a": clean_series(df[y]), "b": clean_series(df[g])}).dropna()
    if work["a"].nunique() < 2 or work["b"].nunique() < 2:
        raise HTTPException(status_code=400, detail="两个变量都至少需要两个类别。")
    table = pd.crosstab(work["a"], work["b"])
    chi2, p, dof, expected = stats.chi2_contingency(table)
    rows: List[Dict[str, Any]] = []
    for idx in table.index:
        row: Dict[str, Any] = {str(y): str(idx)}
        for col in table.columns:
            row[str(col)] = int(table.loc[idx, col])
        rows.append(row)
    return response(req, "卡方检验", "Chi-square test", len(work), stats={"chi_square": round_float(chi2), "p_value": p_value(p), "df": int(dof)}, result_table=rows, diagnostics={"rows": table.index.astype(str).tolist(), "columns": table.columns.astype(str).tolist(), "expected_min": round_float(expected.min())}, warnings=["如果期望频数过低，建议合并类别或使用 Fisher 精确检验。"], summary="已完成两个分类变量的卡方关联检验。")
