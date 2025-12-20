# ai_services/ai_platform/llm/cost_calculator.py
from typing import Dict
from .schemas import UsageStats, CostReport


class CostCalculator:
    """
    [V4 Refactored] 成本计算器。
    基于 Pydantic Schema 进行类型安全的成本核算。
    """

    def __init__(self, pricing_data: Dict, usd_to_rmb_rate: float):
        self.pricing_data = pricing_data
        self.usd_to_rmb_rate = usd_to_rmb_rate
        # 预排序，确保最长前缀匹配
        self.sorted_pricing_keys = sorted(
            self.pricing_data.keys(),
            key=len,
            reverse=True
        )

    def calculate(self, usage_stats: UsageStats) -> CostReport:
        """
        计算成本。
        Args:
            usage_stats: 标准化的用量统计对象
        Returns:
            CostReport: 包含金额的完整报告
        """
        prompt_tokens = usage_stats.prompt_tokens
        completion_tokens = usage_stats.completion_tokens
        cached_tokens = getattr(usage_stats, 'cached_tokens', 0)
        model_key = usage_stats.model_used

        # 1. 匹配定价策略
        matched_key = None
        for key in self.sorted_pricing_keys:
            if key in model_key:
                matched_key = key
                break

        # 兜底逻辑：未找到定价则成本为0
        if not matched_key:
            return CostReport(usage=usage_stats, cost_usd=0.0, cost_rmb=0.0)

        pricing_info = self.pricing_data[matched_key]

        # 2. 确定分层单价
        threshold = pricing_info.get("threshold", 999999999)
        # 注意：通常分层是基于 Total Input (Prompt + Cached) 还是仅 Prompt？
        # Google 计费通常按 Prompt 长度定 Tier。这里暂维持原逻辑，仅用 prompt_tokens 判断 Tier。
        if prompt_tokens <= threshold:
            tier = pricing_info.get("standard", {})
        else:
            tier = pricing_info.get("long", pricing_info.get("standard", {}))

        # 3. 计算 (单价 per 1M)
        # 公式：(Prompt * InputPrice) + (Output * OutputPrice) + (Cached * CachedPrice)
        input_cost = (prompt_tokens / 1_000_000) * tier.get("input", 0)
        output_cost = (completion_tokens / 1_000_000) * tier.get("output", 0)

        cached_price = tier.get("cached", 0)
        cached_cost = (cached_tokens / 1_000_000) * cached_price

        total_usd = round(input_cost + output_cost + cached_cost, 6)
        total_rmb = round(total_usd * self.usd_to_rmb_rate, 6)

        return CostReport(
            usage=usage_stats,
            cost_usd=total_usd,
            cost_rmb=total_rmb
        )