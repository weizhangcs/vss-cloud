# ai_services/ai_platform/llm/schemas.py
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field


class UsageStats(BaseModel):
    """
    [Infrastructure Schema] 标准化用量统计。
    从 Gemini API Response 中提取的原始 Token 数据。
    """
    model_used: str = Field(..., description="实际调用的模型名称")
    prompt_tokens: int = Field(0, description="输入 Token 数")
    cached_tokens: int = Field(0, description="缓存 Input Token 数 (通常费率更低)")
    completion_tokens: int = Field(0, description="输出 Token 数")
    total_tokens: int = Field(0, description="总 Token 数")

    # 性能指标
    duration_seconds: float = Field(0.0, description="API 调用耗时(秒)")
    request_count: int = Field(1, description="请求次数(含重试)")
    timestamp: Optional[str] = Field(None, description="完成时间(ISO8601)")


class CostReport(BaseModel):
    """
    [Infrastructure Schema] 标准化成本报告。
    由 CostCalculator 计算得出，包含金额信息。
    """
    usage: UsageStats
    cost_usd: float = Field(0.0, description="美元成本")
    cost_rmb: float = Field(0.0, description="人民币成本")

    def to_dict(self) -> Dict[str, Any]:
        """兼容旧版业务代码的辅助方法"""
        data = self.usage.model_dump()
        data.update({
            "cost_usd": self.cost_usd,
            "cost_rmb": self.cost_rmb
        })
        return data