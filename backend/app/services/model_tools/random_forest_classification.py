from fastapi import HTTPException
from sklearn.ensemble import RandomForestClassifier

from app.schemas.analysis import AnalysisRunRequest, AnalysisResponse
from app.services.model_tools.prediction_evaluation import evaluate_classifier, feature_importance_from_model, permutation_importance_rows
from app.services.model_tools.shared import dataset_to_dataframe, ensure_columns, is_classification_column, type_hint, build_design_matrix, common_warnings, diagnostics_with_vif, response, class_count


def _opt_int(req: AnalysisRunRequest, key: str, default: int, lower: int = 1, upper: int = 10000) -> int:
    try:
        value = int((req.options or {}).get(key, default))
    except Exception:
        value = default
    return max(lower, min(upper, value))


def run(req: AnalysisRunRequest) -> AnalysisResponse:
    df, dataset = dataset_to_dataframe(req.dataset_id)
    if not req.dependent_variable:
        raise HTTPException(status_code=400, detail="随机森林分类需要选择因变量。")
    x_cols = list(dict.fromkeys((req.independent_variables or []) + (req.control_variables or [])))
    if not x_cols:
        raise HTTPException(status_code=400, detail="随机森林分类至少需要一个自变量。")
    ensure_columns(df, [req.dependent_variable] + x_cols, "随机森林变量")
    if not is_classification_column(df[req.dependent_variable], type_hint(req, req.dependent_variable)):
        raise HTTPException(status_code=400, detail="随机森林分类要求因变量为二分类或多分类变量。")

    dep_classes = class_count(df[req.dependent_variable])
    y, X, meta, build_warnings = build_design_matrix(df, req.dependent_variable, x_cols, req, multiclass=True)
    n_estimators = _opt_int(req, "n_estimators", 300, 10, 3000)
    max_depth_raw = (req.options or {}).get("max_depth", None)
    try:
        max_depth = int(max_depth_raw) if max_depth_raw not in [None, "", "none", "None"] else None
    except Exception:
        max_depth = None
    min_samples_leaf = _opt_int(req, "min_samples_leaf", 1, 1, 100)
    class_weight = (req.options or {}).get("class_weight", "balanced" if dep_classes == 2 else None)
    if class_weight in ["none", "None", "", None]:
        class_weight = None

    estimator = RandomForestClassifier(
        n_estimators=n_estimators,
        random_state=int((req.options or {}).get("random_state", 42)),
        max_depth=max_depth,
        min_samples_leaf=min_samples_leaf,
        class_weight=class_weight,
        n_jobs=-1,
    )
    try:
        fitted, pred_metrics, pred_rows, eval_warnings = evaluate_classifier(estimator, X, y, req)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="随机森林分类失败：{}".format(exc))

    importances = feature_importance_from_model(fitted, X.columns.tolist())
    if not importances:
        importances = permutation_importance_rows(fitted, X, y, "classification", random_state=int((req.options or {}).get("random_state", 42)))
    train = pred_metrics.get("train") or {}; test = pred_metrics.get("test") or {}; cvm = pred_metrics.get("cross_validation") or {}
    stats_out = {
        "task_type": pred_metrics.get("task_type"),
        "class_count": pred_metrics.get("class_count"),
        "accuracy_in_sample": train.get("accuracy"),
        "auc_in_sample": train.get("auc"),
        "test_accuracy": test.get("accuracy"),
        "test_auc": test.get("auc"),
        "test_f1": test.get("f1"),
        "cv_accuracy_mean": cvm.get("cv_accuracy_mean"),
        "n_estimators": n_estimators,
        "max_depth": max_depth,
        "min_samples_leaf": min_samples_leaf,
    }
    warnings = build_warnings + eval_warnings + common_warnings(dataset, int(meta["n_rows_used"]), int(X.shape[1])) + ["随机森林分类属于预测模型；变量重要性表示预测贡献，不等同于统计显著性或因果效应。"]
    diagnostics, vif_warnings_list = diagnostics_with_vif({
        "n_rows_original": int(meta["n_rows_original"]),
        "n_rows_used": int(meta["n_rows_used"]),
        "n_rows_dropped": int(meta["n_rows_dropped"]),
        "predictor_count": int(X.shape[1]),
        "design_matrix_columns": X.columns.tolist(),
        "feature_importances": importances,
        "y_meta": meta.get("y_meta", {}),
        "model_options": req.options or {},
    }, X)
    warnings.extend(vif_warnings_list)
    return response(req, "随机森林分类", "RandomForestClassifier", int(meta["n_rows_used"]), stats=stats_out, result_table=importances, diagnostics=diagnostics, prediction_metrics=pred_metrics, feature_importance=importances, prediction_table=pred_rows, warnings=warnings, summary="已完成随机森林分类/多分类、预测评估与变量重要性计算。")
