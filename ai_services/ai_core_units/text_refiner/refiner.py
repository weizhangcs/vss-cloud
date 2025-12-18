import logging
from pathlib import Path
from typing import Optional
from ai_services.ai_platform.llm.gemini_processor import GeminiProcessor

logger = logging.getLogger(__name__)

class TextRefiner:
    """
    [Core Unit] 通用文本精炼器 (Generic Text Refiner).
    职责：接收一段文本和提示词模版，调用 LLM 进行重写/缩写。
    特点：不感知业务上下文 (Scene/Duration)，只关注文本处理。
    """
    MAX_RETRIES = 2

    def __init__(self, gemini_processor: GeminiProcessor):
        self.gemini = gemini_processor
        self._base_dir = Path(__file__).resolve().parent
        self._prompts_dir = self._base_dir / "prompts"

    def load_template(self, template_name: str, lang: str = "zh") -> str:
        """
        [New Capability] 自主加载内置提示词模版。
        Args:
            template_name: 模版文件名 (不含扩展名和语言后缀), e.g., "narration_refine"
            lang: 语言代码
        """
        # 尝试加载指定语言
        file_name = f"{template_name}_{lang}.txt"
        file_path = self._prompts_dir / file_name

        # 兜底英文
        if not file_path.exists() and lang != 'en':
            logger.warning(f"Template {file_name} not found, falling back to 'en'.")
            file_path = self._prompts_dir / f"{template_name}_en.txt"

        if not file_path.exists():
            logger.error(f"Refiner template not found: {file_path}")
            return ""

        try:
            return file_path.read_text(encoding='utf-8')
        except Exception as e:
            logger.error(f"Failed to read template {file_path}: {e}")
            return ""

    def refine_content(self,
                       content: str,
                       prompt_template: str,
                       model_name: str,
                       **prompt_kwargs) -> Optional[str]:
        """
        执行精炼逻辑。
        Args:
            content: 原始文本
            prompt_template: 包含 {original_text} 占位符的模版
            model_name: 使用的模型
            **prompt_kwargs: 填充模版的其他参数 (如 style_desc, max_seconds 等)

        Returns:
            refined_text (str) or None (if failed)
        """
        if not content:
            return None

        # 构造最终 Prompt
        try:
            prompt = prompt_template.format(original_text=content, **prompt_kwargs)
        except KeyError as e:
            logger.error(f"Prompt formatting failed in TextRefiner: Missing key {e}")
            return None

        for i in range(self.MAX_RETRIES):
            try:
                response, _ = self.gemini.generate_content(
                    model_name=model_name,
                    prompt=prompt,
                    temperature=0.3  # 精炼任务需要较低温度保持稳定
                )
                refined_text = response.get("refined_text", "")

                if refined_text:
                    return refined_text

            except Exception as e:
                logger.warning(f"Refine attempt {i} failed: {e}")

        logger.warning("TextRefiner failed to produce valid output after retries.")
        return None