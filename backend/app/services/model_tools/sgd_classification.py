from fastapi import HTTPException
from sklearn.linear_model import SGDClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from app.schemas.analysis import AnalysisRunRequest, AnalysisResponse
from app.services.model_tools.prediction_evaluation import evaluate_classifier, feature_importance_from_model, permutation_importance_rows
from app.services.model_tools.shared import dataset_to_dataframe, ensure_columns, is_classification_column, type_hint, build_design_matrix, common_warnings, diagnostics_with_vif, response, class_count


def _opt_float(req: AnalysisRunRequest, key: str, default: float, lower: float, upper: float) -> float:
    try:
        return max(lower, min(upper, float((req.options or {}).get(key, default))))
    except Exception:
        return default


def _opt_int(req: AnalysisRunRequest, key: str, default: int, lower: int, upper: int) -> int:
    try:
        return max(lower, min(upper, int((req.options or {}).get(key, default))))
    except Exception:
        return default


def run(req: AnalysisRunRequest) -> AnalysisResponse:
    df, dataset = dataset_to_dataframe(req.dataset_id)
    if not req.dependent_variable:
        raise HTTPException(status_code=400, detail="SGD 分类需要选择因变量。")
    x_cols = list(dict.fromkeys((req.independent_variables or []) + (req.control_variables or [])))
    if not x_cols:
        raise HTTPException(status_code=400, detail="SGD 分类至少需要一个自变量。")
    ensure_columns(df, [req.dependent_variable] + x_cols, "SGD 分类变量")
    if not is_classification_column(df[req.dependent_variable], type_hint(req, req.dependent_variable)):
        raise HTTPException(status_code=400, detail="SGD 分类要求因变量为二分类或多分类变量。")
    dep_classes = class_count(df[req.dependent_variable])
    y, X, meta, build_warnings = build_design_matrix(df, req.dependent_variable, x_cols, req, multiclass=True)
    penalty = str((req.options or {}).get("penalty", "elasticnet"))
    if penalty not in {"l2", "l1", "elasticnet"}:
        penalty = "elasticnet"
    alpha = _opt_float(req, "alpha", 0.0001, 1e-8, 10.0)
    max_iter = _opt_int(req, "max_iter", 2000, 100, 100000)
    class_weight = (req.options or {}).get("class_weight", "balanced" if dep_classes == 2 else None)
    if class_weight in ["none", "None", "", None]:
        class_weight = None
    estimator = Pipeline([
        ("scaler", StandardScaler()),
        ("sgd", SGDClassifier(loss="log_loss", penalty=penalty, alpha=alpha, max_iter=max_iter, random_state=int((req.options or {}).get("random_state", 42)), tol=1e-3, class_weight=class_weight)),
    ])
    try:
        fitted, pred_metrics, pred_rows, eval_warnings = evaluate_classifier(estimator, X, y, req)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="SGD 分类失败：{}".format(exc))
    importances = feature_importance_from_model(fitted.named_steps.get("sgd"), X.columns.tolist()) or permutation_importance_rows(fitted, X, y, "classification", random_state=int((req.options or {}).get("random_state", 42)))
    train = pred_metrics.get("train") or {}; test = pred_metrics.get("test") or {}; cvm = pred_metrics.get("cross_validation") or {}
    stats_out = {"task_type": pred_metrics.get("task_type"), "class_count": dep_classes, "train_accuracy": train.get("accuracy"), "test_accuracy": test.get("accuracy"), "test_f1": test.get("f1"), "cv_accuracy_mean": cvm.get("cv_accuracy_mean"), "penalty": penalty, "alpha": alpha, "max_iter": max_iter}
    warnings = build_warnings + eval_warnings + common_warnings(dataset, int(meta["n_rows_used"]), int(X.shape[1])) + ["SGD 分类适合高维或较大样本分类任务；系数反映预测方向，不等同于传统显著性。"]
    diagnostics, vif_warnings_list = diagnostics_with_vif({"n_rows_original": int(meta["n_rows_original"]), "n_rows_used": int(meta["n_rows_used"]), "n_rows_dropped": int(meta["n_rows_dropped"]), "predictor_count": int(X.shape[1]), "design_matrix_columns": X.columns.tolist(), "y_meta": meta.get("y_meta", {}), "model_options": req.options or {}}, X)
    warnings.extend(vif_warnings_list)
    return response(req, "SGD 分类", "SGDClassifier", int(meta["n_rows_used"]), stats=stats_out, result_table=importances, diagnostics=diagnostics, prediction_metrics=pred_metrics, feature_importance=importances, prediction_table=pred_rows, warnings=warnings, summary="已完成 SGD 二分类/多分类、预测评估与系数重要性计算。")
