from typing import Any, Dict, List
from collections import Counter

from app.schemas.analysis import AnalysisRunRequest, AnalysisResponse
from app.services.model_tools.shared import dataset_to_dataframe, selected_columns, clean_text, is_missing, numeric_series, is_numeric_column, type_hint, round_float, response


def run(req: AnalysisRunRequest) -> AnalysisResponse:
    df, dataset = dataset_to_dataframe(req.dataset_id)
    columns = selected_columns(req, df)
    output: List[Dict[str, Any]] = []
    for col in columns:
        raw = df[col].map(clean_text)
        non_missing = raw[~raw.map(is_missing)]
        numeric = numeric_series(raw)
        numeric_valid = numeric.dropna()
        hint = type_hint(req, col)
        item: Dict[str, Any] = {
            "variable": col,
            "declared_type": hint,
            "count": int(len(non_missing)),
            "missing_count": int(len(raw) - len(non_missing)),
            "missing_rate": round_float((len(raw) - len(non_missing)) / max(1, len(raw))),
            "unique_count": int(non_missing.nunique()),
        }
        if len(numeric_valid) > 0 and is_numeric_column(raw, hint):
            item.update({
                "type": "numeric",
                "mean": round_float(numeric_valid.mean()),
                "std": round_float(numeric_valid.std(ddof=1)) if len(numeric_valid) > 1 else 0.0,
                "min": round_float(numeric_valid.min()),
                "q1": round_float(numeric_valid.quantile(0.25)),
                "median": round_float(numeric_valid.median()),
                "q3": round_float(numeric_valid.quantile(0.75)),
                "max": round_float(numeric_valid.max()),
            })
        else:
            vc = non_missing.value_counts().head(10)
            item.update({
                "type": "categorical",
                "top_values": [{"value": str(k), "count": int(v), "rate": round_float(v / max(1, len(raw)))} for k, v in vc.items()],
            })
        output.append(item)
    return response(req, "描述统计", "Descriptive statistics", len(df), descriptive_statistics=output, result_table=output, diagnostics={"columns": columns}, summary="已完成 {} 个变量的描述统计。".format(len(output)))
