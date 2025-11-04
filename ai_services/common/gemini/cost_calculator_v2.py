# 文件名: cost_calculator.py
# 描述: [重构后] 一个独立的、无状态的成本计算器。
# 它接收所有必要的计算参数，不依赖任何外部配置系统。

from typing import Dict

class CostCalculator:
    """
    一个独立的成本计算器 (v3 - Decoupled)。
    它通过构造函数接收所有必要的计算参数（定价数据、汇率），
    不依赖任何外部配置系统，实现了完全解耦。
    """

    def __init__(self, pricing_data: Dict, usd_to_rmb_rate: float):
        """
        初始化成本计算器。

        Args:
            pricing_data (Dict): 包含模型定价信息的字典。
            usd_to_rmb_rate (float): 美元到人民币的汇率。
        """
        self.pricing_data = pricing_data
        self.usd_to_rmb_rate = usd_to_rmb_rate
        # 对价格表的key按长度降序排序，确保优先匹配最精确的名称
        self.sorted_pricing_keys = sorted(
            self.pricing_data.keys(),
            key=len,
            reverse=True
        )

    def calculate(self, model_name: str, usage_data: Dict) -> Dict:
        """
        根据给定的用量数据和模型名称，计算成本。
        :param model_name: 调用的模型名称 (e.g., 'gemini-1.5-pro-latest')
        :param usage_data: 包含 'prompt_tokens' 和 'completion_tokens' 的字典。
        :return: 包含 'cost_usd' 和 'cost_rmb' 的字典。
        """
        prompt_tokens = usage_data.get("prompt_tokens", 0)
        completion_tokens = usage_data.get("completion_tokens", 0)

        matched_key = None
        for key in self.sorted_pricing_keys:
            if key in model_name:
                matched_key = key
                break

        if not matched_key:
            return {"cost_usd": 0.0, "cost_rmb": 0.0, "warning": f"No pricing info found for model: {model_name}"}

        # 使用注入的定价数据
        pricing_info = self.pricing_data[matched_key]

        if prompt_tokens <= pricing_info.get("threshold", 9999999):
            tier_pricing = pricing_info.get("standard", {})
        else:
            tier_pricing = pricing_info.get("long", pricing_info.get("standard", {}))

        input_cost = (prompt_tokens / 1_000_000) * tier_pricing.get("input", 0)
        output_cost = (completion_tokens / 1_000_000) * tier_pricing.get("output", 0)

        cost_usd = input_cost + output_cost
        # 使用注入的汇率
        cost_rmb = cost_usd * self.usd_to_rmb_rate

        return {"cost_usd": round(cost_usd, 6), "cost_rmb": round(cost_rmb, 6)}