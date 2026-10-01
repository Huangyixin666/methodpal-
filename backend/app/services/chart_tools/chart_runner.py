import uuid
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

from app.schemas.chart import ChartRecommendation, ChartSpec
from app.services.store import ANALYSES, CHARTS, DATASETS


def _analysis_dict(analysis_id: str) -> Dict[str, Any]:
    item = ANALYSES.get(analysis_id)
    if not item:
        raise HTTPException(status_code=404, detail="analysis not found: {}".format(analysis_id))
    if hasattr(item, "model_dump"):
        return item.model_dump()
    if isinstance(item, dict):
        return item
    raise HTTPException(status_code=400, detail="analysis format is invalid")


def _dataset_dict(dataset_id: str) -> Dict[str, Any]:
    item = DATASETS.get(dataset_id)
    if not item:
        raise HTTPException(status_code=404, detail="dataset not found: {}".format(dataset_id))
    if hasattr(item, "model_dump"):
        return item.model_dump()
    if isinstance(item, dict):
        return item
    raise HTTPException(status_code=400, detail="dataset format is invalid")


def _dataset_rows(dataset: Dict[str, Any]) -> Tuple[List[str], List[List[Any]]]:
    headers = [str(x) for x in (dataset.get("headers") or [])]
    rows = dataset.get("_full_rows") or dataset.get("rows") or []
    out: List[List[Any]] = []
    width = len(headers)
    for row in rows:
        if isinstance(row, dict):
            out.append([row.get(h, "") for h in headers])
        else:
            vals = list(row or [])
            out.append((vals + [""] * max(0, width - len(vals)))[:width])
    return headers, out


def _to_float_cell(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        text = str(value).strip()
        if text == "" or text.lower() in {"nan", "none", "null", "缺失"}:
            return None
        return float(text)
    except Exception:
        return None


def _dataset_correlation_chart(dataset_id: str, options: Dict[str, Any]) -> ChartSpec:
    dataset = _dataset_dict(dataset_id)
    headers, rows = _dataset_rows(dataset)
    if not headers or not rows:
        raise HTTPException(status_code=400, detail="当前数据集为空，无法生成相关矩阵。")

    max_vars = int(options.get("max_vars", 18) or 18)
    numeric: List[Tuple[str, List[Optional[float]]]] = []
    declared = {c.get('name'): c.get('type') for c in (dataset.get('profile') or {}).get('columns', [])}
    for ci, name in enumerate(headers):
        if declared.get(name) in ('ID型', '分类型', '二分类', '文本型', '有序型'):
            continue
        vals: List[Optional[float]] = []
        ok = 0
        for row in rows:
            v = _to_float_cell(row[ci] if ci < len(row) else None)
            vals.append(v)
            if v is not None:
                ok += 1
        if ok >= max(5, int(len(rows) * 0.6)):
            valid = [v for v in vals if v is not None]
            if len(set(round(x, 12) for x in valid)) > 1:
                numeric.append((name, vals))
    if len(numeric) < 2:
        raise HTTPException(status_code=400, detail="当前数据集中可用于相关矩阵的数值变量少于 2 个。")

    # Prefer variables with larger variance and cap for readability.
    def var_score(item: Tuple[str, List[float]]) -> float:
        vals = item[1]
        mean = sum(vals) / len(vals)
        return sum((x - mean) ** 2 for x in vals) / max(1, len(vals) - 1)
    numeric = numeric[:max(2, min(max_vars, 40))]
    labels = [x[0] for x in numeric]
    cols = [x[1] for x in numeric]

    def corr(a: List[float], b: List[float]) -> Optional[float]:
        paired = [(x, y) for x, y in zip(a, b) if x is not None and y is not None]
        if len(paired) < 5:
            return None
        a, b = zip(*paired)
        ma = sum(a) / len(a)
        mb = sum(b) / len(b)
        num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
        da = sum((x - ma) ** 2 for x in a)
        db = sum((y - mb) ** 2 for y in b)
        if da <= 0 or db <= 0:
            return None
        return round(num / ((da * db) ** 0.5), 4)

    matrix: List[List[Any]] = []
    for a in cols:
        matrix.append([corr(a, b) for b in cols])

    return ChartSpec(
        chart_id=_new_chart_id(),
        analysis_id="dataset:{}".format(dataset_id),
        chart_type="correlation_heatmap",
        title="相关矩阵热图",
        subtitle="按数据列顺序展示最多 {} 个数值变量，缺失值采用成对删除。".format(max_vars),
        matrix=matrix,
        row_labels=labels,
        column_labels=labels,
        notes=["该图基于数据集直接生成，不依赖当前模型。相关性不等于因果关系。",
               "使用 Pearson 相关；排除 ID、类别编码、文本及有序量表，不对缺失值做均值填补。每对字段至少 5 个有效样本。"],
        meta={"dataset_id": dataset_id, "source": "dataset"},
    )


def _num(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        out = float(value)
        if out != out or out in [float("inf"), float("-inf")]:
            return None
        return round(out, 6)
    except Exception:
        return None


def _first_number(row: Dict[str, Any], keys: List[str]) -> Optional[float]:
    for key in keys:
        if key in row:
            n = _num(row.get(key))
            if n is not None:
                return n
    return None


def _safe_name(value: Any, fallback: str = "变量") -> str:
    text = str(value if value is not None else "").strip()
    return text or fallback


def _top(rows: List[Dict[str, Any]], key: str, limit: int) -> List[Dict[str, Any]]:
    def score(x: Dict[str, Any]) -> float:
        n = _num(x.get(key))
        return abs(n or 0.0)
    return sorted(rows, key=score, reverse=True)[:limit]


def _rec(chart_type: str, title: str, description: str, reason: str, priority: int) -> ChartRecommendation:
    return ChartRecommendation(
        chart_type=chart_type,
        title=title,
        description=description,
        reason=reason,
        priority=priority,
        available=True,
    )


def recommend_charts(analysis_id: Optional[str], max_charts: int = 8, dataset_id: Optional[str] = None, context: Optional[Dict[str, Any]] = None) -> Tuple[List[ChartRecommendation], List[str]]:
    context = context or {}
    ana = _analysis_dict(analysis_id) if analysis_id else {}
    warnings: List[str] = []
    recs: List[ChartRecommendation] = []

    if dataset_id:
        recs.append(_rec(
            "correlation_heatmap",
            "相关矩阵热图",
            "基于当前数据集直接查看数值变量相关性。",
            "相关性矩阵不依赖当前模型，可直接从数据集生成。",
            80,
        ))

    if ana.get("feature_importance"):
        recs.append(_rec(
            "feature_importance_bar",
            "变量重要性图",
            "展示模型中预测贡献最高的变量。",
            "当前分析结果包含 feature_importance。",
            95,
        ))
    pm = ana.get("prediction_metrics") or {}
    if pm.get("confusion_matrix"):
        recs.append(_rec(
            "confusion_matrix_heatmap",
            "混淆矩阵热图",
            "查看分类模型具体错在哪些类别。",
            "当前分析结果包含 confusion_matrix。",
            90,
        ))
    if ana.get("model_comparison"):
        recs.append(_rec(
            "model_comparison_bar",
            "模型对比图",
            "比较不同模型的测试集/交叉验证表现。",
            "当前分析结果包含 model_comparison。",
            88,
        ))
    if ana.get("regression_table"):
        rows = ana.get("regression_table") or []
        if any(row.get("odds_ratio") is not None for row in rows if isinstance(row, dict)):
            recs.append(_rec(
                "odds_ratio_bar",
                "OR 值图",
                "展示 Logistic/分类解释模型的优势比方向。",
                "系数表中包含 odds_ratio。",
                86,
            ))
        recs.append(_rec(
            "coefficient_bar",
            "系数方向图",
            "展示回归系数大小和正负方向。",
            "当前分析结果包含 regression_table。",
            82,
        ))
    if ana.get("prediction_table"):
        recs.append(_rec(
            "prediction_scatter",
            "预测值 vs 真实值",
            "用于查看预测模型拟合是否偏离真实值。",
            "当前分析结果包含 prediction_table。",
            75,
        ))
    if ana.get("correlation_matrix"):
        recs.append(_rec(
            "correlation_heatmap",
            "相关矩阵热图",
            "展示数值变量之间的相关性结构。",
            "当前分析结果包含 correlation_matrix。",
            72,
        ))
    if ana.get("descriptive_statistics"):
        recs.append(_rec(
            "descriptive_mean_bar",
            "描述统计均值图",
            "快速比较各数值变量的均值。",
            "当前分析结果包含 descriptive_statistics。",
            55,
        ))

    recs = sorted(recs, key=lambda x: x.priority, reverse=True)
    deduped: List[ChartRecommendation] = []
    seen = set()
    for rec in recs:
        if rec.chart_type in seen:
            continue
        seen.add(rec.chart_type)
        deduped.append(rec)
    recs = deduped
    if not recs:
        warnings.append("当前项目没有足够结构化字段生成图表。建议先运行回归、分类模型、模型对比，或提供数据集生成相关矩阵。")
    return recs[: max(1, int(max_charts or 8))], warnings


def _new_chart_id() -> str:
    return "chart_{}".format(uuid.uuid4().hex[:10])


def _feature_importance_chart(ana: Dict[str, Any], options: Dict[str, Any]) -> ChartSpec:
    limit = int(options.get("top_n", 15) or 15)
    aggregate = bool(options.get("aggregate_original", True))
    rows = [r for r in (ana.get("feature_importance") or []) if isinstance(r, dict)]
    if not rows:
        raise HTTPException(status_code=400, detail="当前分析结果没有 feature_importance，无法生成变量重要性图。")

    if aggregate:
        grouped: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            name = _safe_name(row.get("original_variable") or row.get("variable"))
            importance = _num(row.get("importance")) or 0.0
            if name not in grouped:
                grouped[name] = {"feature": name, "importance": 0.0, "encoded_features": 0}
            grouped[name]["importance"] += abs(importance)
            grouped[name]["encoded_features"] += 1
        data = list(grouped.values())
        for item in data:
            item["importance"] = round(item["importance"], 6)
    else:
        data = [
            {
                "feature": _safe_name(row.get("variable") or row.get("original_variable")),
                "importance": _num(row.get("importance")) or 0.0,
                "original_variable": row.get("original_variable"),
            }
            for row in rows
        ]
    data = sorted(data, key=lambda x: abs(float(x.get("importance") or 0.0)), reverse=True)[:limit]
    data.reverse()
    return ChartSpec(
        chart_id=_new_chart_id(),
        analysis_id=str(ana.get("analysis_id") or ""),
        chart_type="feature_importance_bar",
        title="变量重要性 / 预测贡献",
        subtitle="{} · 重要性越高，表示对预测贡献越大；不等同于因果效应。".format(ana.get("model_name") or "模型"),
        x_key="feature",
        y_key="importance",
        x_label="变量",
        y_label="重要性",
        layout="vertical",
        data=data,
        series=[{"data_key": "importance", "label": "重要性"}],
        notes=["变量重要性表示预测贡献，不代表统计显著性或因果关系。"],
    )


def _confusion_matrix_chart(ana: Dict[str, Any], options: Dict[str, Any]) -> ChartSpec:
    pm = ana.get("prediction_metrics") or {}
    matrix = pm.get("confusion_matrix") or []
    if not matrix:
        raise HTTPException(status_code=400, detail="当前分析结果没有 confusion_matrix。")
    y_meta = (ana.get("diagnostics") or {}).get("y_meta") or {}
    labels = y_meta.get("classes") or [str(i) for i in range(len(matrix))]
    labels = [str(x) for x in labels]
    return ChartSpec(
        chart_id=_new_chart_id(),
        analysis_id=str(ana.get("analysis_id") or ""),
        chart_type="confusion_matrix_heatmap",
        title="混淆矩阵",
        subtitle="行表示真实类别，列表示预测类别。对角线越高，分类越准确。",
        matrix=matrix,
        row_labels=labels,
        column_labels=labels,
        notes=["混淆矩阵能显示模型具体把哪些类别混淆。"],
    )


def _model_comparison_chart(ana: Dict[str, Any], options: Dict[str, Any]) -> ChartSpec:
    rows = [r for r in (ana.get("model_comparison") or []) if isinstance(r, dict)]
    if not rows:
        raise HTTPException(status_code=400, detail="当前分析结果没有 model_comparison。")
    metric_candidates = [
        ("test_auc", "测试 AUC"),
        ("cv_roc_auc_mean", "CV AUC"),
        ("test_accuracy", "测试 Accuracy"),
        ("cv_accuracy_mean", "CV Accuracy"),
        ("test_f1", "测试 F1"),
        ("cv_f1_mean", "CV F1"),
        ("test_r2", "测试 R²"),
        ("cv_r2_mean", "CV R²"),
        ("test_rmse", "测试 RMSE"),
        ("test_mae", "测试 MAE"),
    ]
    metric_key, metric_label = None, None
    for key, label in metric_candidates:
        if any(_num(r.get(key)) is not None for r in rows):
            metric_key, metric_label = key, label
            break
    if not metric_key:
        raise HTTPException(status_code=400, detail="模型对比表没有可绘制的数值指标。")
    data = []
    for row in rows:
        value = _num(row.get(metric_key))
        if value is None:
            continue
        data.append({"model": _safe_name(row.get("model") or row.get("model_name")), "metric": value})
    data = sorted(data, key=lambda x: x["metric"], reverse=not str(metric_key).endswith("rmse") and not str(metric_key).endswith("mae"))[:12]
    data.reverse()
    return ChartSpec(
        chart_id=_new_chart_id(),
        analysis_id=str(ana.get("analysis_id") or ""),
        chart_type="model_comparison_bar",
        title="模型对比：{}".format(metric_label),
        subtitle="用于比较预测表现；不同指标的解释方向可能不同。",
        x_key="model",
        y_key="metric",
        x_label="模型",
        y_label=metric_label,
        layout="vertical",
        data=data,
        series=[{"data_key": "metric", "label": metric_label}],
        notes=["模型对比是预测性能比较，不等于因果或显著性比较。"],
        meta={"metric_key": metric_key},
    )


def _coefficient_chart(ana: Dict[str, Any], options: Dict[str, Any], odds: bool = False) -> ChartSpec:
    rows = [r for r in (ana.get("regression_table") or []) if isinstance(r, dict)]
    if not rows:
        raise HTTPException(status_code=400, detail="当前分析结果没有 regression_table。")
    key = "odds_ratio" if odds else "coefficient"
    candidates = []
    for row in rows:
        value = _num(row.get(key))
        if value is None:
            continue
        var = _safe_name(row.get("variable") or row.get("term") or row.get("name"))
        if var.lower() in {"const", "intercept", "截距"}:
            continue
        candidates.append({"variable": var, "value": value, "p_value": _num(row.get("p_value"))})
    if not candidates:
        raise HTTPException(status_code=400, detail="系数表中没有可绘制的数值。")
    candidates = sorted(candidates, key=lambda x: abs(x["value"] if not odds else (x["value"] - 1.0)), reverse=True)[: int(options.get("top_n", 15) or 15)]
    candidates.reverse()
    title = "OR 值图" if odds else "回归系数方向图"
    label = "OR" if odds else "系数"
    notes = ["OR > 1 表示相对于参照组的发生优势更高；OR < 1 表示更低。"] if odds else ["系数正负表示方向，大小受变量尺度影响。"]
    return ChartSpec(
        chart_id=_new_chart_id(),
        analysis_id=str(ana.get("analysis_id") or ""),
        chart_type="odds_ratio_bar" if odds else "coefficient_bar",
        title=title,
        subtitle="{} · 仅展示可绘制的前若干项。".format(ana.get("model_name") or "模型"),
        x_key="variable",
        y_key="value",
        x_label="变量",
        y_label=label,
        layout="vertical",
        data=candidates,
        series=[{"data_key": "value", "label": label}],
        notes=notes,
    )


def _prediction_scatter_chart(ana: Dict[str, Any], options: Dict[str, Any]) -> ChartSpec:
    rows = [r for r in (ana.get("prediction_table") or []) if isinstance(r, dict)]
    data = []
    for row in rows:
        yt = _num(row.get("y_true"))
        yp = _num(row.get("y_pred"))
        if yt is None or yp is None:
            continue
        data.append({"true": yt, "predicted": yp, "row": row.get("row_index")})
    if not data:
        raise HTTPException(status_code=400, detail="预测样本中没有可绘制的 y_true/y_pred 数值。")
    return ChartSpec(
        chart_id=_new_chart_id(),
        analysis_id=str(ana.get("analysis_id") or ""),
        chart_type="prediction_scatter",
        title="预测值 vs 真实值",
        subtitle="点越接近对角线，预测越接近真实值。",
        x_key="true",
        y_key="predicted",
        x_label="真实值",
        y_label="预测值",
        data=data,
        series=[{"data_key": "predicted", "label": "预测值"}],
        notes=["该图主要用于回归或数值预测诊断。"],
    )


def _correlation_heatmap(ana: Dict[str, Any], options: Dict[str, Any]) -> ChartSpec:
    corr = ana.get("correlation_matrix") or []
    if not corr:
        raise HTTPException(status_code=400, detail="当前分析结果没有 correlation_matrix。")
    # Common shape: [{variable: x, A: 1, B: .2}, ...]
    if isinstance(corr, list) and corr and isinstance(corr[0], dict):
        first = corr[0]
        label_key = "variable" if "variable" in first else "变量" if "变量" in first else list(first.keys())[0]
        rows = [_safe_name(r.get(label_key)) for r in corr]
        cols = [k for k in first.keys() if k != label_key]
        matrix = []
        for r in corr:
            matrix.append([_num(r.get(c)) for c in cols])
    else:
        raise HTTPException(status_code=400, detail="相关矩阵格式暂无法绘图。")
    return ChartSpec(
        chart_id=_new_chart_id(),
        analysis_id=str(ana.get("analysis_id") or ""),
        chart_type="correlation_heatmap",
        title="相关矩阵热图",
        subtitle="展示变量之间线性相关方向和强度。",
        matrix=matrix,
        row_labels=rows,
        column_labels=cols,
        notes=["相关不等于因果；高相关变量可能带来共线性。"],
    )


def _descriptive_mean_chart(ana: Dict[str, Any], options: Dict[str, Any]) -> ChartSpec:
    rows = [r for r in (ana.get("descriptive_statistics") or []) if isinstance(r, dict)]
    data = []
    for row in rows:
        name = _safe_name(row.get("variable") or row.get("变量") or row.get("name"))
        mean = _first_number(row, ["mean", "均值", "Mean"])
        if mean is None:
            continue
        data.append({"variable": name, "mean": mean})
    if not data:
        raise HTTPException(status_code=400, detail="描述统计表中没有可绘制的均值。")
    data = sorted(data, key=lambda x: abs(float(x["mean"])), reverse=True)[: int(options.get("top_n", 15) or 15)]
    data.reverse()
    return ChartSpec(
        chart_id=_new_chart_id(),
        analysis_id=str(ana.get("analysis_id") or ""),
        chart_type="descriptive_mean_bar",
        title="数值变量均值比较",
        subtitle="用于快速查看变量平均水平；不同单位变量不宜直接比较大小。",
        x_key="variable",
        y_key="mean",
        x_label="变量",
        y_label="均值",
        layout="vertical",
        data=data,
        series=[{"data_key": "mean", "label": "均值"}],
        notes=["如果变量单位不同，均值比较只能作概览，不代表变量重要性。"],
    )


CHART_BUILDERS = {
    "feature_importance_bar": _feature_importance_chart,
    "confusion_matrix_heatmap": _confusion_matrix_chart,
    "model_comparison_bar": _model_comparison_chart,
    "coefficient_bar": lambda ana, opts: _coefficient_chart(ana, opts, odds=False),
    "odds_ratio_bar": lambda ana, opts: _coefficient_chart(ana, opts, odds=True),
    "prediction_scatter": _prediction_scatter_chart,
    "correlation_heatmap": _correlation_heatmap,
    "descriptive_mean_bar": _descriptive_mean_chart,
}


def generate_chart(analysis_id: Optional[str], chart_type: str, options: Optional[Dict[str, Any]] = None) -> ChartSpec:
    from app.services.artifact_versions import dataset_snapshot, analysis_is_current
    options = options or {}
    dataset_id = options.get("dataset_id") or options.get("_dataset_id")
    ctype = (chart_type or "").strip()

    # Dataset-level charts do not require changing/rerunning the current analysis.
    if ctype == "correlation_heatmap" and dataset_id:
        chart = _dataset_correlation_chart(str(dataset_id), options)
        from app.services.store import DATASETS
        chart.meta.update(dataset_snapshot(DATASETS.get(str(dataset_id)) or {}))
        chart.meta.update({'scope': 'dataset', 'artifact_status': 'current'})
        CHARTS[chart.chart_id] = chart.model_dump()
        return chart

    if not analysis_id:
        raise HTTPException(status_code=400, detail="生成该图表需要 analysis_id。若要画相关矩阵，请提供 dataset_id。")
    ana = _analysis_dict(str(analysis_id))
    from app.services.store import DATASETS
    actual_dataset = DATASETS.get(ana.get('dataset_id')) or {}
    if dataset_id and str(dataset_id) != ana.get('dataset_id'):
        raise HTTPException(409, '图表的分析与当前数据集不匹配，请先重新分析。')
    if not analysis_is_current(ana, actual_dataset):
        raise HTTPException(409, '分析结果已过期，请先基于当前数据重新运行模型。')
    if ctype == "auto":
        recs, _ = recommend_charts(str(analysis_id), max_charts=1, dataset_id=dataset_id)
        if not recs:
            raise HTTPException(status_code=400, detail="没有可自动生成的图表。")
        ctype = recs[0].chart_type
    builder = CHART_BUILDERS.get(ctype)
    if not builder:
        raise HTTPException(status_code=400, detail="暂不支持该图表类型：{}".format(chart_type))
    chart = builder(ana, options or {})
    chart.meta.update(dataset_snapshot(actual_dataset))
    chart.meta.update({'scope': 'analysis', 'config_fingerprint': (ana.get('source_snapshot') or {}).get('config_fingerprint'), 'artifact_status': 'current'})
    CHARTS[chart.chart_id] = chart.model_dump()
    return chart


def get_chart(chart_id: str) -> ChartSpec:
    item = CHARTS.get(chart_id)
    if not item:
        raise HTTPException(status_code=404, detail="chart not found")
    return ChartSpec(**item)
