import unittest

from app.services.langgraph_agent.autonomy_policy import (
    infer_analysis_goal,
    infer_target_from_text,
    normalize_variable_type,
    parse_column_list,
    target_type_is_ambiguous,
)


class AutonomyPolicyTests(unittest.TestCase):
    def test_infers_analysis_goal(self):
        self.assertEqual(infer_analysis_goal("我想预测学生是否毕业"), "prediction")
        self.assertEqual(infer_analysis_goal("哪些因素与幸福感有关"), "explanation")
        self.assertEqual(infer_analysis_goal("比较不同地区的收入差异"), "comparison")
        self.assertEqual(infer_analysis_goal("描述样本的年龄分布"), "description")
        self.assertEqual(infer_analysis_goal("评估培训的因果效应"), "causal")

    def test_matches_target_and_column_list(self):
        names = ["幸福感", "年龄", "家庭收入"]
        self.assertEqual(infer_target_from_text("预测幸福感", names), "幸福感")
        self.assertEqual(parse_column_list("年龄、家庭收入", names), ["年龄", "家庭收入"])

    def test_flags_small_integer_scale_as_semantically_ambiguous(self):
        column = {"name": "满意度", "type": "整数型", "unique_count": 5}
        self.assertTrue(target_type_is_ambiguous(column))
        self.assertFalse(target_type_is_ambiguous(column, "有序型"))
        self.assertEqual(normalize_variable_type("Likert"), "有序型")


if __name__ == "__main__":
    unittest.main()
