from django.db import models
from django.utils.translation import gettext_lazy as _
from model_utils.models import TimeStampedModel


class TagDefinition(TimeStampedModel):
    """
    [Configuration] 动态标签定义表 (V4.1 I18n Support)
    核心逻辑：
    1. name: 系统内部唯一标识 (Canonical Key)，如 'warm'。
    2. synonyms: 入口词汇大杂烩，包含各语种变体，如 ['cozy', '温馨']。
    3. label_*: 用于前端展示的官方名称。
    """
    CATEGORY_CHOICES = (
        ('visual_mood', 'Visual Mood'),
        ('shot_type', 'Shot Type'),
    )

    # Core Identity
    name = models.CharField(_("Canonical Key"), max_length=50, unique=True, db_index=True,
                            help_text="系统唯一标识 (e.g., 'warm')")
    category = models.CharField(_("Category"), max_length=50, choices=CATEGORY_CHOICES, db_index=True)

    # Display Labels
    label_zh = models.CharField(_("Label ZH"), max_length=50, blank=True, help_text="中文显示名 (e.g., '温暖')")
    label_en = models.CharField(_("Label EN"), max_length=50, blank=True, help_text="英文显示名 (e.g., 'Warm')")

    # Mapping Rules
    synonyms = models.JSONField(_("Entry Mapping"), default=list, blank=True,
                                help_text="入口映射词表 (包含中英文变体, e.g., ['cozy', '温馨'])")

    is_active = models.BooleanField(_("Is Active"), default=True)

    class Meta:
        verbose_name = _("Tag Definition")
        verbose_name_plural = _("Tag Definitions")
        ordering = ['category', 'name']

    def __str__(self):
        return f"[{self.category}] {self.name} ({self.label_zh})"