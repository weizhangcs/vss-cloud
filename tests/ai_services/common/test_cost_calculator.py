# tests/ai_services/common/test_cost_calculator.py

import unittest
from ai_services.ai_platform.llm.cost_calculator import CostCalculator
from typing import Dict, Any


class CostCalculatorTests(unittest.TestCase):
    """
    针对 ai_services.common.gemini.cost_calculator_v2.py 的单元测试。
    """

    # 模拟核心定价表 (基于 core/settings.py 中的默认值)
    MOCK_PRICING: Dict[str, Any] = {
        "gemini-1.5-pro": {
            "threshold": 128000,
            "standard": {"input": 3.50, "output": 10.50},  # USD/M Tokens
            "long": {"input": 7.00, "output": 21.00}
        },
        "gemini-1.5-flash": {
            "threshold": 128000,
            "standard": {"input": 0.075, "output": 0.30},
            "long": {"input": 0.15, "output": 0.60}
        }
    }
    MOCK_RATE = 7.25  # 模拟汇率

    def setUp(self):
        """为每个测试用例初始化 CostCalculator 实例"""
        self.calculator = CostCalculator(
            pricing_data=self.MOCK_PRICING,
            usd_to_rmb_rate=self.MOCK_RATE
        )

    def test_01_standard_pricing_flash(self):
        """测试低于阈值的 Flash 模型定价 (标准层)"""
        usage = {"prompt_tokens": 100000, "completion_tokens": 10000}
        model = "gemini-1.5-flash-latest"

        # 预期计算:
        # Input USD: 100000 / 1M * 0.075 = 0.0075
        # Output USD: 10000 / 1M * 0.30  = 0.003
        # Total USD: 0.0105
        # Total RMB: 0.0105 * 7.25 = 0.076125  # <-- 修正了注释中的计算结果

        result = self.calculator.calculate(model, usage)
        self.assertAlmostEqual(result["cost_usd"], 0.0105, 5)
        self.assertAlmostEqual(result["cost_rmb"], 0.0761, 4)  # <-- 将 0.0759 修正为 0.0761
        self.assertNotIn("warning", result)

    def test_02_long_pricing_pro(self):
        """测试高于阈值的 Pro 模型定价 (长上下文层)"""
        usage = {"prompt_tokens": 200000, "completion_tokens": 30000}
        model = "gemini-1.5-pro-latest"

        # 预期计算 (使用 Long Tier: Input 7.00, Output 21.00)
        # Input USD: 200000 / 1M * 7.00 = 1.40
        # Output USD: 30000 / 1M * 21.00 = 0.63
        # Total USD: 2.03
        # Total RMB: 2.03 * 7.25 = 14.7175

        result = self.calculator.calculate(model, usage)
        self.assertAlmostEqual(result["cost_usd"], 2.03, 2)
        self.assertAlmostEqual(result["cost_rmb"], 14.7175, 4)
        self.assertNotIn("warning", result)

    def test_03_partial_match_pricing(self):
        """测试模型名称部分匹配 (例如，模型名中带有版本信息)"""
        usage = {"prompt_tokens": 1000, "completion_tokens": 100}
        model = "gemini-1.5-flash-v001"  # 带有版本号

        # 预期计算 (使用 Standard Tier: Input 0.075, Output 0.30)
        # Input USD: 1000 / 1M * 0.075 = 0.000075
        # Output USD: 100 / 1M * 0.30  = 0.000030
        # Total USD: 0.000105

        result = self.calculator.calculate(model, usage)
        self.assertAlmostEqual(result["cost_usd"], 0.000105, 6)
        self.assertNotIn("warning", result)

    def test_04_no_pricing_info(self):
        """测试模型无匹配定价信息 (应返回警告)"""
        usage = {"prompt_tokens": 1000, "completion_tokens": 100}
        model = "claude-3-opus"

        result = self.calculator.calculate(model, usage)
        self.assertEqual(result["cost_usd"], 0.0)
        self.assertIn("warning", result)

    def test_05_zero_tokens(self):
        """测试零 token 用量"""
        usage = {"prompt_tokens": 0, "completion_tokens": 0}
        model = "gemini-1.5-pro-latest"

        result = self.calculator.calculate(model, usage)
        self.assertEqual(result["cost_usd"], 0.0)
        self.assertEqual(result["cost_rmb"], 0.0)
        self.assertNotIn("warning", result)


if __name__ == '__main__':
    # 注意: 如果你使用 Django 的 manage.py test 运行，请忽略这一行
    unittest.main()