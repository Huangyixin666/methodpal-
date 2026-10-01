from typing import Any, Dict, List, Optional

from app.services.store import DATASETS


def get_dataset(dataset_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not dataset_id:
        return None
    return DATASETS.get(dataset_id)


def get_dataset_summary(dataset_id: Optional[str]) -> Dict[str, Any]:
    dataset = get_dataset(dataset_id)
    if not dataset:
        return {
            "found": False,
            "message": "当前没有可用数据集。请先上传数据。",
        }

    profile = dataset.get("profile") or {}
    columns = profile.get("columns") or []

    return {
        "found": True,
        "dataset_id": dataset.get("dataset_id"),
        "dataset_revision": dataset.get("dataset_revision", 0),
        "data_fingerprint": dataset.get("data_fingerprint"),
        "name": dataset.get("name"),
        "row_count": profile.get("row_count", len(dataset.get("rows") or [])),
        "column_count": profile.get("column_count", len(dataset.get("headers") or [])),
        "issue_count": profile.get("issue_count", 0),
        "missing_count": profile.get("missing_count", 0),
        "outlier_count": profile.get("outlier_count", 0),
        "summary": profile.get("summary"),
        "columns": [
            {
                "name": c.get("name"),
                "type": c.get("type"),
                "missing_rate": c.get("missing_rate", 0),
                "unique_count": c.get("unique_count", 0),
                "role_suggestion": c.get("role_suggestion"),
                "numeric_rate": c.get("numeric_rate", 0),
                "top_values": c.get("top_values", [])[:5],
            }
            for c in columns
        ],
    }


def summarize_dataset_quality(summary: Dict[str, Any]) -> List[str]:
    warnings: List[str] = []
    if not summary.get("found"):
        return [summary.get("message") or "当前没有可用数据集。"]

    rows = int(summary.get("row_count") or 0)
    issues = int(summary.get("issue_count") or 0)
    missing = int(summary.get("missing_count") or 0)
    outliers = int(summary.get("outlier_count") or 0)

    if rows < 30:
        warnings.append("样本量较小，正式统计推断和机器学习预测都需要谨慎。")
    if missing > 0:
        warnings.append("数据中存在缺失值，建议先确认缺失比例和处理策略。")
    if outliers > 0:
        warnings.append("数据中存在潜在异常值，建议先复查或保留敏感性分析。")
    if issues == 0:
        warnings.append("当前未发现明显数据质量问题，但仍建议检查变量编码和量表方向。")
    return warnings
