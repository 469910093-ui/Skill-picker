"""分类 fixture 回归：名称强路由、多标签、历史误分类元凶。"""

import unittest

from fixtures import RULES

import matching


class TestCategorize(unittest.TestCase):
    def _cat(self, name, desc=""):
        return matching.categorize(name, desc, RULES)

    def test_figma_name_route(self):
        primary, labels = self._cat("figma-generate-design",
                                    "translate app page into Figma, review changes, create file")
        self.assertEqual(primary, "设计/Figma",
                         "figma-* 必须名称强路由，不受描述里 review/create 干扰")

    def test_figjam_contains_route(self):
        primary, _ = self._cat("some-figjam-helper", "whatever")
        self.assertEqual(primary, "设计/Figma")

    def test_aws_name_route(self):
        primary, _ = self._cat("aws-cdk", "Authors AWS infrastructure")
        self.assertEqual(primary, "云/AWS/运维")

    def test_baidu_map_not_design(self):
        primary, labels = self._cat("baidu-ai-map",
                                    "百度地图为 Agent 场景原生设计的地图能力，AI 路线规划")
        self.assertNotEqual(primary, "设计/Figma", "描述含「设计」二字不应误入设计分类")
        self.assertEqual(primary, "出行/电商业务")

    def test_mckinsey_not_design(self):
        primary, _ = self._cat("mckinsey-consultant",
                               "生成McKinsey风格研究报告和PPT，Dummy Page设计")
        self.assertNotEqual(primary, "设计/Figma")
        self.assertEqual(primary, "PPT/演示")

    def test_multilabel(self):
        primary, labels = self._cat("gochina-weekly-review",
                                    "GOCHINA 五产线周度经营复盘（飞书 DocxXML）AntV 结论图")
        self.assertGreaterEqual(len(labels), 2, f"周复盘应命中多个标签: {labels}")
        self.assertEqual(primary, labels[0])
        self.assertIn("周报/复盘/数据分析", labels)

    def test_fallback(self):
        primary, labels = self._cat("mystery-tool", "does absolutely nothing describable")
        self.assertEqual(primary, RULES["fallback_category"])
        self.assertEqual(labels, [RULES["fallback_category"]])


if __name__ == "__main__":
    unittest.main()
