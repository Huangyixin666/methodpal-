import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.inspection import permutation_importance
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    mean_absolute_error,
    mean_squared_error,
    precision_score,
    r2_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import KFold, StratifiedKFold, cross_validate, train_test_split

from app.schemas.analysis import AnalysisRunRequest
from app.services.model_tools.shared import round_float


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def evaluation_options(req: AnalysisRunRequest, n_samples: int) -> Dict[str, Any]:
    opts = req.options or {}
    random_state = _safe_int(opts.get("random_state", 42), 42)
    test_size = _safe_float(opts.get("test_size", 0.25), 0.25)
    test_size = min(max(test_size, 0.1), 0.5)
    cv_folds = _safe_int(opts.get("cv_folds", 5), 5)
    cv_folds = max(2, min(cv_folds, max(2, n_samples)))
    return {
        "random_state": random_state,
        "test_size": test_size,
        "cv_folds": cv_folds,
    }


def can_split(n_samples: int, n_features: int, task: str, y: Optional[pd.Series] = None) -> Tuple[bool, str]:
    if n_samples < 10:
        return False, "样本量少于 10，当前仅返回训练集内拟合表现；测试集划分不稳定。"
    if n_samples <= n_features + 5:
        return False, "样本量相对预测变量过少，测试集划分不稳定；当前仅返回训练集内拟合表现。"
    if task == "classification" and y is not None:
        counts = pd.Series(y).value_counts()
        if len(counts) < 2 or counts.min() < 2:
            return False, "分类任务中至少一个类别样本过少，无法稳定划分训练集/测试集。"
    return True, ""


def _regression_metrics(y_true: Any, y_pred: Any) -> Dict[str, Any]:
    return {
        "r2": round_float(r2_score(y_true, y_pred)),
        "rmse": round_float(mean_squared_error(y_true, y_pred) ** 0.5),
        "mae": round_float(mean_absolute_error(y_true, y_pred)),
        "mse": round_float(mean_squared_error(y_true, y_pred)),
    }


def _classification_metrics(y_true: Any, y_pred: Any, y_prob: Any = None) -> Dict[str, Any]:
    y_true_arr = np.asarray(y_true)
    classes = np.unique(y_true_arr)
    is_binary = len(classes) == 2
    avg = "binary" if is_binary else "weighted"
    out = {
        "class_count": int(len(classes)),
        "accuracy": round_float(accuracy_score(y_true, y_pred)),
        "precision": round_float(precision_score(y_true, y_pred, zero_division=0, average=avg)),
        "recall": round_float(recall_score(y_true, y_pred, zero_division=0, average=avg)),
        "f1": round_float(f1_score(y_true, y_pred, zero_division=0, average=avg)),
    }
    if y_prob is not None:
        try:
            prob_arr = np.asarray(y_prob)
            if is_binary:
                if len(prob_arr.shape) == 2 and prob_arr.shape[1] >= 2:
                    out["auc"] = round_float(roc_auc_score(y_true, prob_arr[:, 1]))
                else:
                    out["auc"] = round_float(roc_auc_score(y_true, prob_arr))
            else:
                if len(prob_arr.shape) == 2 and prob_arr.shape[1] == len(classes):
                    out["auc_ovr_weighted"] = round_float(roc_auc_score(y_true, prob_arr, multi_class="ovr", average="weighted"))
                out["auc"] = out.get("auc_ovr_weighted")
        except Exception:
            out["auc"] = None
    else:
        out["auc"] = None
    return out


def _predict_prob(estimator: Any, X: pd.DataFrame) -> Optional[np.ndarray]:
    try:
        if hasattr(estimator, "predict_proba"):
            proba = estimator.predict_proba(X)
            if proba is not None:
                return np.asarray(proba)
        if hasattr(estimator, "decision_function"):
            score = estimator.decision_function(X)
            return np.asarray(score)
    except Exception:
        return None
    return None

def _cv_summary(scores: Dict[str, np.ndarray], prefix: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, values in scores.items():
        if not key.startswith("test_"):
            continue
        name = key.replace("test_", "")
        arr = np.asarray(values, dtype=float)
        arr = arr[~np.isnan(arr)]
        if arr.size == 0:
            continue
        if name.startswith("neg_"):
            name = name.replace("neg_", "")
            arr = -arr
        out["{}_{}_mean".format(prefix, name)] = round_float(float(arr.mean()))
        out["{}_{}_std".format(prefix, name)] = round_float(float(arr.std(ddof=1))) if arr.size > 1 else 0.0
    return out


def prediction_rows(y_true: pd.Series, y_pred: Any, split: str, max_rows: int = 50) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    y_pred_arr = np.asarray(y_pred)
    for i, (idx, true_value) in enumerate(y_true.items()):
        if i >= max_rows:
            break
        rows.append({
            "row_index": int(idx) if isinstance(idx, (int, np.integer)) else str(idx),
            "split": split,
            "y_true": round_float(true_value),
            "y_pred": round_float(y_pred_arr[i]) if i < len(y_pred_arr) else None,
            "residual": round_float(float(true_value) - float(y_pred_arr[i])) if i < len(y_pred_arr) else None,
        })
    return rows


def classification_prediction_rows(y_true: pd.Series, y_pred: Any, y_prob: Any, split: str, max_rows: int = 50) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    y_pred_arr = np.asarray(y_pred)
    prob_arr = np.asarray(y_prob) if y_prob is not None else None
    for i, (idx, true_value) in enumerate(y_true.items()):
        if i >= max_rows:
            break
        item = {
            "row_index": int(idx) if isinstance(idx, (int, np.integer)) else str(idx),
            "split": split,
            "y_true": int(true_value),
            "y_pred": int(y_pred_arr[i]) if i < len(y_pred_arr) else None,
        }
        if prob_arr is not None and i < len(prob_arr):
            try:
                if len(prob_arr.shape) == 2:
                    pred_class = int(item["y_pred"]) if item.get("y_pred") is not None else 0
                    if 0 <= pred_class < prob_arr.shape[1]:
                        item["probability"] = round_float(prob_arr[i, pred_class])
                    item["probabilities"] = [round_float(v) for v in prob_arr[i].tolist()[:10]]
                else:
                    item["probability"] = round_float(prob_arr[i])
            except Exception:
                pass
        rows.append(item)
    return rows

def evaluate_regressor(estimator: Any, X: pd.DataFrame, y: pd.Series, req: AnalysisRunRequest) -> Tuple[Any, Dict[str, Any], List[Dict[str, Any]], List[str]]:
    warnings: List[str] = []
    opts = evaluation_options(req, len(y))
    n_features = int(X.shape[1])

    fitted = clone(estimator)
    fitted.fit(X, y)
    train_pred = fitted.predict(X)
    metrics: Dict[str, Any] = {
        "task_type": "regression",
        "evaluation_protocol": "train_only",
        "train": _regression_metrics(y, train_pred),
        "test": {},
        "cross_validation": {},
    }
    rows = prediction_rows(y, train_pred, "train", max_rows=30)

    ok_split, reason = can_split(len(y), n_features, "regression", y)
    if not ok_split:
        warnings.append(reason)
        return fitted, metrics, rows, warnings

    try:
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=opts["test_size"], random_state=opts["random_state"]
        )
        split_est = clone(estimator)
        split_est.fit(X_train, y_train)
        train_pred_split = split_est.predict(X_train)
        test_pred = split_est.predict(X_test)
        metrics["evaluation_protocol"] = "train_test_cv"
        metrics["train"] = _regression_metrics(y_train, train_pred_split)
        metrics["test"] = _regression_metrics(y_test, test_pred)
        rows = prediction_rows(y_test, test_pred, "test", max_rows=50)
    except Exception as exc:
        warnings.append("训练/测试集评估失败，已保留训练集内结果：{}".format(exc))

    try:
        cv = min(opts["cv_folds"], len(y))
        if cv >= 2:
            scores = cross_validate(
                clone(estimator), X, y,
                scoring={"r2": "r2", "neg_rmse": "neg_root_mean_squared_error", "neg_mae": "neg_mean_absolute_error"},
                cv=KFold(n_splits=cv, shuffle=True, random_state=opts["random_state"]),
                error_score=np.nan,
            )
            metrics["cross_validation"] = _cv_summary(scores, "cv")
            metrics["cross_validation"]["folds"] = cv
    except Exception as exc:
        warnings.append("交叉验证失败：{}".format(exc))

    return fitted, metrics, rows, warnings


def evaluate_classifier(estimator: Any, X: pd.DataFrame, y: pd.Series, req: AnalysisRunRequest) -> Tuple[Any, Dict[str, Any], List[Dict[str, Any]], List[str]]:
    warnings: List[str] = []
    opts = evaluation_options(req, len(y))
    n_features = int(X.shape[1])
    y_int = y.astype(int)
    class_count = int(pd.Series(y_int).nunique())
    if class_count < 2:
        raise ValueError("分类任务至少需要两个类别。")

    fitted = clone(estimator)
    fitted.fit(X, y_int)
    train_pred = fitted.predict(X)
    train_prob = _predict_prob(fitted, X)
    metrics: Dict[str, Any] = {
        "task_type": "classification" if class_count == 2 else "multiclass_classification",
        "class_count": class_count,
        "evaluation_protocol": "train_only",
        "train": _classification_metrics(y_int, train_pred, train_prob),
        "test": {},
        "cross_validation": {},
        "confusion_matrix": confusion_matrix(y_int, train_pred).tolist(),
    }
    rows = classification_prediction_rows(y_int, train_pred, train_prob, "train", max_rows=30)

    ok_split, reason = can_split(len(y_int), n_features, "classification", y_int)
    if not ok_split:
        warnings.append(reason)
        return fitted, metrics, rows, warnings

    try:
        X_train, X_test, y_train, y_test = train_test_split(
            X, y_int, test_size=opts["test_size"], random_state=opts["random_state"], stratify=y_int
        )
        split_est = clone(estimator)
        split_est.fit(X_train, y_train)
        train_pred_split = split_est.predict(X_train)
        train_prob_split = _predict_prob(split_est, X_train)
        test_pred = split_est.predict(X_test)
        test_prob = _predict_prob(split_est, X_test)
        metrics["evaluation_protocol"] = "train_test_cv"
        metrics["train"] = _classification_metrics(y_train, train_pred_split, train_prob_split)
        metrics["test"] = _classification_metrics(y_test, test_pred, test_prob)
        metrics["confusion_matrix"] = confusion_matrix(y_test, test_pred).tolist()
        rows = classification_prediction_rows(y_test, test_pred, test_prob, "test", max_rows=50)
    except Exception as exc:
        warnings.append("训练/测试集评估失败，已保留训练集内结果：{}".format(exc))

    try:
        counts = pd.Series(y_int).value_counts()
        cv = min(opts["cv_folds"], int(counts.min()))
        if cv >= 2:
            if class_count == 2:
                scoring = {"accuracy": "accuracy", "f1": "f1", "roc_auc": "roc_auc"}
            else:
                scoring = {"accuracy": "accuracy", "f1_weighted": "f1_weighted"}
            scores = cross_validate(
                clone(estimator), X, y_int,
                scoring=scoring,
                cv=StratifiedKFold(n_splits=cv, shuffle=True, random_state=opts["random_state"]),
                error_score=np.nan,
            )
            metrics["cross_validation"] = _cv_summary(scores, "cv")
            metrics["cross_validation"]["folds"] = cv
        else:
            warnings.append("每个类别样本数不足，无法进行稳定的分层交叉验证。")
    except Exception as exc:
        warnings.append("交叉验证失败：{}".format(exc))

    return fitted, metrics, rows, warnings

def feature_importance_from_coefficients(columns: List[str], coefficients: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for name, coef in zip(columns, coefficients):
        c = float(coef)
        rows.append({
            "variable": str(name),
            "importance": round_float(abs(c)),
            "coefficient": round_float(c),
            "direction": "positive" if c > 0 else "negative" if c < 0 else "zero",
            "original_variable": str(name).split("_")[0],
        })
    return sorted(rows, key=lambda x: x.get("importance") or 0, reverse=True)


def feature_importance_from_model(model: Any, columns: List[str]) -> List[Dict[str, Any]]:
    if hasattr(model, "feature_importances_"):
        return sorted([
            {"variable": str(c), "importance": round_float(v), "original_variable": str(c).split("_")[0]}
            for c, v in zip(columns, model.feature_importances_)
        ], key=lambda x: x.get("importance") or 0, reverse=True)
    if hasattr(model, "coef_"):
        coef = model.coef_
        if len(np.asarray(coef).shape) > 1:
            coef = np.asarray(coef)[0]
        return feature_importance_from_coefficients(columns, coef)
    return []


def permutation_importance_rows(estimator: Any, X: pd.DataFrame, y: pd.Series, task: str, random_state: int = 42) -> List[Dict[str, Any]]:
    if len(y) < 8:
        return []
    try:
        scoring = "r2" if task == "regression" else "accuracy"
        result = permutation_importance(estimator, X, y, n_repeats=10, random_state=random_state, scoring=scoring)
        rows = []
        for name, mean, std in zip(X.columns, result.importances_mean, result.importances_std):
            rows.append({
                "variable": str(name),
                "importance": round_float(mean),
                "importance_std": round_float(std),
                "method": "permutation_importance",
                "original_variable": str(name).split("_")[0],
            })
        return sorted(rows, key=lambda x: x.get("importance") or 0, reverse=True)
    except Exception:
        return []


def flatten_regression_metrics(model_name: str, metrics: Dict[str, Any]) -> Dict[str, Any]:
    train = metrics.get("train") or {}
    test = metrics.get("test") or {}
    cv = metrics.get("cross_validation") or {}
    return {
        "model": model_name,
        "task_type": "regression",
        "train_r2": train.get("r2"),
        "test_r2": test.get("r2"),
        "cv_r2_mean": cv.get("cv_r2_mean"),
        "cv_r2_std": cv.get("cv_r2_std"),
        "test_rmse": test.get("rmse"),
        "test_mae": test.get("mae"),
        "protocol": metrics.get("evaluation_protocol"),
    }


def flatten_classification_metrics(model_name: str, metrics: Dict[str, Any]) -> Dict[str, Any]:
    train = metrics.get("train") or {}
    test = metrics.get("test") or {}
    cv = metrics.get("cross_validation") or {}
    return {
        "model": model_name,
        "task_type": "classification",
        "train_accuracy": train.get("accuracy"),
        "test_accuracy": test.get("accuracy"),
        "class_count": metrics.get("class_count"),
        "test_auc": test.get("auc"),
        "test_f1": test.get("f1"),
        "cv_accuracy_mean": cv.get("cv_accuracy_mean"),
        "cv_f1_mean": cv.get("cv_f1_mean") or cv.get("cv_f1_weighted_mean"),
        "cv_roc_auc_mean": cv.get("cv_roc_auc_mean"),
        "protocol": metrics.get("evaluation_protocol"),
    }
