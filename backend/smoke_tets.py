import json
import sys
import urllib.error
import urllib.request


BASE_URL = "http://localhost:8000"


def request(method, path, body=None, expected_status=200):
    url = BASE_URL + path
    data = None
    headers = {}

    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(
        url=url,
        data=data,
        headers=headers,
        method=method,
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            text = resp.read().decode("utf-8")
            status = resp.status
            parsed = json.loads(text) if text else None
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8")
        status = e.code
        try:
            parsed = json.loads(text) if text else None
        except Exception:
            parsed = text
    except Exception as e:
        raise RuntimeError("请求失败: {} {} -> {}".format(method, path, e))

    if status != expected_status:
        raise AssertionError(
            "状态码错误: {} {} 期望 {} 实际 {} 返回 {}".format(
                method, path, expected_status, status, parsed
            )
        )

    return parsed


def must_have(obj, keys, name):
    for k in keys:
        if k not in obj:
            raise AssertionError("{} 缺少字段: {}".format(name, k))


def assert_non_empty(value, name):
    if value is None or value == "" or value == []:
        raise AssertionError("{} 为空".format(name))


def main():
    print("=== MethodPal backend smoke test ===")

    # 1. health
    health = request("GET", "/api/health")
    must_have(health, ["ok", "service", "message"], "health")
    assert health["ok"] is True
    print("[OK] health")

    # 2. projects
    projects = request("GET", "/api/projects")
    assert isinstance(projects, list)
    assert_non_empty(projects, "projects")
    print("[OK] list projects")

    new_project = request(
        "POST",
        "/api/projects",
        {
            "name": "接口测试项目",
            "description": "smoke test",
            "research_goal": "影响因素分析",
        },
    )
    must_have(new_project, ["project_id", "name", "progress"], "create project")
    project_id = new_project["project_id"]
    print("[OK] create project:", project_id)

    project_detail = request("GET", "/api/projects/{}".format(project_id))
    assert project_detail["project_id"] == project_id
    print("[OK] get project")

    # 3. datasets
    sample = request("GET", "/api/datasets/sample")
    must_have(sample, ["dataset_id", "headers", "rows", "cells", "profile", "message"], "sample dataset")
    dataset_id = sample["dataset_id"]
    headers = sample["headers"]
    assert_non_empty(headers, "sample headers")
    assert_non_empty(sample["rows"], "sample rows")
    print("[OK] sample dataset:", dataset_id)

    profile = request("GET", "/api/datasets/{}/profile".format(dataset_id))
    must_have(profile, ["row_count", "column_count", "issue_count", "columns"], "dataset profile")
    print("[OK] dataset profile")

    suggestions = request("POST", "/api/datasets/{}/clean/suggest".format(dataset_id))
    must_have(suggestions, ["dataset_id", "suggestions", "count"], "clean suggest")
    print("[OK] clean suggest")

    cleaned = request("POST", "/api/datasets/{}/clean/apply".format(dataset_id), {"strategy": "accept_suggestions"})
    must_have(cleaned, ["dataset_id", "headers", "rows", "cells", "profile", "message"], "clean apply")
    print("[OK] clean apply")

    pasted = request(
        "POST",
        "/api/datasets/paste",
        {
            "name": "pasted_test",
            "headers": ["编号", "年龄", "性别", "家庭支持", "学业压力", "睡眠时间", "心理健康得分"],
            "rows": [
                ["001", "20", "女", "4.2", "3.8", "6.5", "72"],
                ["002", "22", "男", "3.5", "4.1", "5.8", "65"],
                ["003", "19", "女", "4.8", "2.9", "7.2", "80"],
            ],
        },
    )
    must_have(pasted, ["dataset_id", "headers", "rows", "profile"], "paste dataset")
    print("[OK] paste dataset")

    # 4. variables
    variables = request(
        "POST",
        "/api/variables/infer",
        {
            "dataset_id": dataset_id,
            "columns": headers,
            "research_goal": "影响因素分析",
        },
    )
    must_have(
        variables,
        ["dependent_candidates", "independent_candidates", "control_candidates", "message"],
        "variables infer",
    )
    print("[OK] variables infer")

    saved_vars = request(
        "POST",
        "/api/variables/save",
        {
            "project_id": project_id,
            "dataset_id": dataset_id,
            "dependent_variable": "心理健康得分",
            "independent_variables": ["家庭支持", "学业压力", "睡眠时间"],
            "control_variables": ["年龄", "性别"],
        },
    )
    must_have(saved_vars, ["ok"], "variables save")
    print("[OK] variables save")

    # 5. methods
    catalog = request("GET", "/api/methods/catalog")
    must_have(catalog, ["methods"], "methods catalog")
    assert_non_empty(catalog["methods"], "methods catalog.methods")
    print("[OK] methods catalog")

    rec = request(
        "POST",
        "/api/methods/recommend",
        {
            "dataset_id": dataset_id,
            "dependent_variable": "心理健康得分",
            "independent_variables": ["家庭支持", "学业压力", "睡眠时间"],
            "control_variables": ["年龄", "性别"],
            "research_goal": "影响因素分析",
        },
    )
    must_have(rec, ["recommended", "message"], "methods recommend")
    assert_non_empty(rec["recommended"], "recommended methods")
    print("[OK] methods recommend")

    # 6. analysis
    analysis = request(
        "POST",
        "/api/analysis/run",
        {
            "dataset_id": dataset_id,
            "model": "linear_regression",
            "dependent_variable": "心理健康得分",
            "independent_variables": ["家庭支持", "学业压力", "睡眠时间"],
            "control_variables": ["年龄", "性别"],
        },
    )
    must_have(
        analysis,
        ["analysis_id", "model_name", "dependent_variable", "method", "stats", "regression_table", "diagnostics", "summary"],
        "analysis run",
    )
    assert_non_empty(analysis["stats"], "analysis.stats")
    assert_non_empty(analysis["regression_table"], "analysis.regression_table")
    analysis_id = analysis["analysis_id"]
    print("[OK] analysis run:", analysis_id)

    analysis_detail = request("GET", "/api/analysis/{}".format(analysis_id))
    must_have(analysis_detail, ["analysis_id", "stats", "regression_table"], "analysis get")
    print("[OK] analysis get")

    # 7. AI explain
    explain = request(
        "POST",
        "/api/ai/explain",
        {
            "analysis_id": analysis_id,
            "style": "academic",
            "analysis_result": analysis,
        },
    )
    must_have(explain, ["style", "text", "key_findings", "warnings", "suggested_next_steps"], "ai explain")
    assert_non_empty(explain["text"], "explain.text")
    print("[OK] ai explain")

    # 8. chat
    chat = request(
        "POST",
        "/api/chat",
        {
            "message": "我下一步应该做什么？",
            "page": "home",
            "context": {
                "dataset_id": dataset_id,
                "analysis_id": analysis_id,
            },
        },
    )
    must_have(chat, ["reply", "source"], "chat")
    assert_non_empty(chat["reply"], "chat.reply")
    print("[OK] chat")

    # 9. reports
    report = request(
        "POST",
        "/api/reports/generate",
        {
            "project_id": project_id,
            "analysis_id": analysis_id,
            "format": "markdown",
            "style": "academic",
        },
    )
    must_have(report, ["report_id", "title", "format", "content", "sections"], "report generate")
    report_id = report["report_id"]
    print("[OK] report generate:", report_id)

    report_detail = request("GET", "/api/reports/{}".format(report_id))
    must_have(report_detail, ["report_id", "content"], "report get")
    print("[OK] report get")

    report_alias = request("GET", "/api/report/{}".format(report_id))
    must_have(report_alias, ["report_id", "content"], "report alias get")
    print("[OK] report alias get")

    # 10. expected 404
    bad_profile = request("GET", "/api/datasets/not_exist/profile", expected_status=404)
    must_have(bad_profile, ["detail"], "expected 404 dataset profile")
    print("[OK] expected 404")

    print("\n全部 smoke tests 通过。后端主要接口当前是可用的。")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("\n[FAIL]", e)
        sys.exit(1)