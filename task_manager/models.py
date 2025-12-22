# task_manager/models.py
import uuid
from django.db import models
from django.utils.translation import gettext_lazy as _
from django.utils import timezone  # [新增] 用于获取当前时间
from model_utils.models import TimeStampedModel
from django_fsm import FSMField, transition

from organization.models import Organization, EdgeInstance


class Task(TimeStampedModel):
    """
    云端任务模型。
    v1.2.0-alpha.3: 深度清理冗余，并增强统计能力。
    """

    class TaskType(models.TextChoices):
        DEPLOY_RAG_CORPUS = "DEPLOY_RAG_CORPUS", _("Deploy RAG Corpus")
        CHARACTER_IDENTIFIER = "CHARACTER_IDENTIFIER", _("Character Identifier")
        GENERATE_NARRATION = "GENERATE_NARRATION", _("Generate Narration")
        GENERATE_DUBBING = "GENERATE_DUBBING", _("Generate Dubbing")
        GENERATE_EDITING_SCRIPT = "GENERATE_EDITING_SCRIPT", _("Generate Editing Script")
        LOCALIZE_NARRATION = "LOCALIZE_NARRATION", _("Localize Narration")
        VISUAL_ANALYSIS = 'visual_analysis', _('Visual Analysis & Refinement')
        SUBTITLE_CONTEXT = 'subtitle_context', 'Subtitle Context Analysis'
        CHARACTER_PRE_ANNOTATOR = 'character_pre_annotator', _('Character Pre-Annotator')
        SCENE_PRE_ANNOTATOR = 'scene_pre_annotator', _('Scene Pre-Annotator')

    class TaskStatus(models.TextChoices):
        PENDING = "PENDING", _("Pending")
        RUNNING = "RUNNING", _("Running")
        COMPLETED = "COMPLETED", _("Completed")
        FAILED = "FAILED", _("Failed")

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="tasks",
        verbose_name=_("Organization")
    )
    assigned_edge = models.ForeignKey(
        EdgeInstance,
        on_delete=models.SET_NULL,
        related_name="tasks",
        null=True,
        blank=True,
        verbose_name=_("Assigned Edge")
    )
    task_type = models.CharField(  # 【关键修改：改为 models.CharField】
        choices=TaskType.choices,  # 使用原生的 choices 属性
        max_length=30,
        verbose_name=_("Task Type")
    )
    status = FSMField(
        default=TaskStatus.PENDING,
        choices=TaskStatus.choices,
        max_length=10,
        verbose_name=_("Status")
    )

    # --- 核心数据 ---
    payload = models.JSONField(_("Payload"), default=dict, blank=True)
    result = models.JSONField(_("Result"), default=dict, blank=True, null=True)
    logs = models.TextField(_("Logs"), blank=True, null=True)

    # --- [新增] 统计字段 ---
    started_at = models.DateTimeField(_("Started At"), null=True, blank=True)
    finished_at = models.DateTimeField(_("Finished At"), null=True, blank=True)
    duration = models.DurationField(_("Duration"), null=True, blank=True, help_text=_("Execution time."))

    class Meta:
        verbose_name = _("Task")
        verbose_name_plural = _("Tasks")
        ordering = ['-created']

    def __str__(self):
        return f"Task {self.id} ({self.task_type}) - {self.status}"

    def _calculate_duration(self):
        """辅助方法：计算耗时"""
        if self.started_at and self.finished_at:
            self.duration = self.finished_at - self.started_at

    # --- 状态机转换逻辑 ---

    @transition(field=status, source=TaskStatus.PENDING, target=TaskStatus.RUNNING)
    def start(self):
        # [新增] 记录开始时间
        self.started_at = timezone.now()

    @transition(field=status, source=TaskStatus.RUNNING, target=TaskStatus.COMPLETED)
    def complete(self, result_data):
        self.result = result_data
        # [新增] 记录结束时间并计算时长
        self.finished_at = timezone.now()
        self._calculate_duration()

    @transition(field=status, source='*', target=TaskStatus.FAILED)
    def fail(self, error_message):
        self.result = {"error": error_message}
        # [新增] 即使失败也要记录结束时间，以便统计失败任务的耗时
        self.finished_at = timezone.now()
        self._calculate_duration()