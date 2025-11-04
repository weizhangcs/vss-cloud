# ai_services/common/gemini/cost_calculator.py

from typing import Dict


class CostCalculator:
    """
    [最终重构] 纯粹的、环境无关的成本计算器。
    所有配置都通过构造函数注入。
    """

    def __init__(self, pricing_table: Dict, usd_to_rmb_rate: float):
        """
        初始化时，接收所有必要的配置作为参数。
        """
        if not pricing_table:
            raise ValueError("定价表 (pricing_table) 不能为空。")

        self.pricing_table = pricing_table
        self.usd_to_rmb_rate = usd_to_rmb_rate

        # 核心优化：对价格表的key按长度降序排序，确保优先匹配最精确的名称
        self.sorted_pricing_keys = sorted(
            self.pricing_table.keys(),
            key=len,
            reverse=True
        )

    def calculate(self, model_name: str, usage_data: Dict) -> Dict:
        """
        根据给定的用量数据和模型名称，计算成本。
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

        pricing_info = self.pricing_table[matched_key]

        if prompt_tokens <= pricing_info.get("threshold", 9999999):
            tier_pricing = pricing_info.get("standard", {})
        else:
            tier_pricing = pricing_info.get("long", pricing_info.get("standard", {}))

        input_cost = (prompt_tokens / 1_000_000) * tier_pricing.get("input", 0)
        output_cost = (completion_tokens / 1_000_000) * tier_pricing.get("output", 0)

        cost_usd = input_cost + output_cost
        cost_rmb = cost_usd * self.usd_to_rmb_rate

        return {"cost_usd": round(cost_usd, 6), "cost_rmb": round(cost_rmb, 6)}