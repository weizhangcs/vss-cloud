# 文件名: ai_services/common/gemini/cost_calculator_v3.py
# 描述: [Vertex AI 价格修正版] 独立的、无状态的成本计算器。
# 修正：更新了价格分层逻辑和价格数据，以匹配 Google Vertex AI 的最新计费模型。

from typing import Dict, Any


class CostCalculator:
    """
    一个独立的成本计算器 (v3 - Vertex AI Ready)。
    其逻辑严格遵循 Google Cloud Vertex AI Generative AI 的官方定价。
    """

    def __init__(self, pricing_data: Dict, usd_to_rmb_rate: float):
        """
        初始化成本计算器。

        Args:
            pricing_data (Dict): 包含模型定价信息的字典 (应使用 Vertex AI 价格)。
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

        :param model_name: (后备参数) 调用的模型名称。
        :param usage_data: 包含 'prompt_tokens', 'completion_tokens' 和 'model_used' 的字典。
        :return: 包含 'cost_usd' 和 'cost_rmb' 的字典。
        """
        prompt_tokens = usage_data.get("prompt_tokens", 0)
        completion_tokens = usage_data.get("completion_tokens", 0)

        # 优先使用 usage_data.model_used，确保与 GeminiProcessor 的报告一致
        model_key_for_pricing = usage_data.get("model_used", model_name)

        matched_key = None
        for key in self.sorted_pricing_keys:
            # 使用更准确的模型名称进行匹配
            if key in model_key_for_pricing:
                matched_key = key
                break

        if not matched_key:
            return {"cost_usd": 0.0, "cost_rmb": 0.0,
                    "warning": f"No pricing info found for model: {model_key_for_pricing}"}

        pricing_info = self.pricing_data[matched_key]

        # --- 核心计费逻辑：根据 Vertex AI 规则处理分层 ---

        # 1. 设置阈值：只有 Pro 模型使用 200K 阈值，其他（如 Flash）使用超大阈值走平价
        if matched_key == "gemini-2.5-pro":  # <-- BUG FIXED: 从 1.5-pro 更改为 2.5-pro
            # Gemini 2.5 Pro 的官方分层阈值是 200,000 tokens
            threshold = pricing_info.get("threshold", 200000)
        else:
            # Flash 或其他平价模型，设置一个理论上达不到的阈值
            threshold = 999999999

            # 2. 选择价格层级
        if prompt_tokens <= threshold:
            # 标准层价格 (适用于所有 Flash 模型，以及 Pro 模型的短上下文)
            tier_pricing = pricing_info.get("standard", {})
        else:
            # 长上下文层价格 (仅适用于 Pro 模型的长上下文)
            tier_pricing = pricing_info.get("long", pricing_info.get("standard", {}))

        # 3. 计算成本
        input_cost = (prompt_tokens / 1_000_000) * tier_pricing.get("input", 0)
        output_cost = (completion_tokens / 1_000_000) * tier_pricing.get("output", 0)

        cost_usd = input_cost + output_cost
        cost_rmb = cost_usd * self.usd_to_rmb_rate

        return {"cost_usd": round(cost_usd, 6), "cost_rmb": round(cost_rmb, 6)}