import logging
from typing import List, Set, Dict, Optional
from django.core.cache import cache
from configuration.models import TagDefinition

logger = logging.getLogger(__name__)


class TagManager:
    """
    [Component] 动态标签管理器 (V4.1 I18n Support)
    职责：
    1. 构建 'Input Token -> Canonical Key' 的全量映射。
    2. 提供归一化服务，无论输入是 '温馨' 还是 'Cozy'，都输出 'warm'。
    """

    _REDIS_PREFIX = "vss:config:tags"

    @classmethod
    def get_cache_keys(cls, category: str):
        return {
            "valid": f"{cls._REDIS_PREFIX}:{category}:valid",  # Set: Canonical Key 白名单
            "mapping": f"{cls._REDIS_PREFIX}:{category}:mapping"  # Hash: Any Token -> Canonical Key
        }

    @classmethod
    def sync_to_redis(cls, category: str = None):
        """从 DB 构建全量映射表"""
        if category:
            categories = [category]
        else:
            categories = list(set(TagDefinition.objects.values_list('category', flat=True)))

        for cat in categories:
            definitions = TagDefinition.objects.filter(category=cat, is_active=True)
            keys = cls.get_cache_keys(cat)

            valid_keys = set()
            mapping_dict = {}

            for tag_def in definitions:
                canonical = tag_def.name.lower().strip()
                valid_keys.add(canonical)

                # 1. Canonical 自身映射 (warm -> warm)
                mapping_dict[canonical] = canonical

                # 2. Display Labels 映射 (Warm -> warm, 温暖 -> warm)
                if tag_def.label_en:
                    mapping_dict[tag_def.label_en.lower().strip()] = canonical
                if tag_def.label_zh:
                    mapping_dict[tag_def.label_zh.lower().strip()] = canonical

                # 3. Synonyms 映射 (cozy -> warm, 温馨 -> warm)
                for syn in tag_def.synonyms:
                    if syn:
                        token = syn.lower().strip()
                        mapping_dict[token] = canonical

            # 写入 Redis (永不过期，直到下次 sync)
            cache.set(keys["valid"], valid_keys, timeout=None)
            cache.set(keys["mapping"], mapping_dict, timeout=None)

            logger.info(f"TagManager: Synced '{cat}' - {len(valid_keys)} keys, {len(mapping_dict)} mappings.")

    @classmethod
    def normalize_tags(cls, raw_tags: List[str], category: str, auto_add_unknown: bool = False) -> List[str]:
        """
        核心清洗方法
        :return: List of Canonical Keys (e.g., ['warm', 'dark'])
        """
        keys = cls.get_cache_keys(category)

        mapping = cache.get(keys["mapping"])
        valid_keys = cache.get(keys["valid"])

        # 缓存击穿兜底
        if mapping is None or valid_keys is None:
            cls.sync_to_redis(category)
            mapping = cache.get(keys["mapping"], {})
            valid_keys = cache.get(keys["valid"], set())

        cleaned_canonical_set = set()

        for raw in raw_tags:
            if not isinstance(raw, str) or not raw.strip():
                continue

            token = raw.lower().strip()

            # 1. 查全量映射表
            if token in mapping:
                cleaned_canonical_set.add(mapping[token])
            # 2. 未知词处理 (开放模式)
            elif auto_add_unknown:
                cleaned_canonical_set.add(token)

        return sorted(list(cleaned_canonical_set))

    @classmethod
    def get_display_label(cls, canonical_key: str, lang: str = 'zh') -> Optional[str]:
        """(Optional Helper) 获取用于展示的文本，Edge 端可能用到"""
        try:
            tag = TagDefinition.objects.get(name=canonical_key)
            return tag.label_zh if lang == 'zh' else tag.label_en
        except TagDefinition.DoesNotExist:
            return canonical_key