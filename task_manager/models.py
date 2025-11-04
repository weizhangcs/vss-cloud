import uuid
from django.db import models
from django.utils.translation import gettext_lazy as _
from model_utils.models import TimeStampedModel
from django_choices_field import TextChoicesField
from django_fsm import FSMField, transition  # 导入 FSM 字段和 transition 装饰器

# 导入我们 App 1 的 Organization 模型
from organization.models import Organization, EdgeInstance

class Task(TimeStampedModel):
    """
    云端任务模型。
    这是云-边协同的核心，使用 django-fsm-2 管理状态。
    """

    class TaskType(models.TextChoices):
        DEPLOY_RAG_CORPUS = "DEPLOY_RAG_CORPUS", _("Deploy RAG Corpus (Cloud)")
        CHARACTER_METRICS = "CHARACTER_METRICS", _("Character Metrics (Cloud)")
        CHARACTER_IDENTIFIER = "CHARACTER_IDENTIFIER", _("Character Identifier (Cloud)")
        CHARACTER_PIPELINE = "CHARACTER_PIPELINE", _("Character Analysis Pipeline (Cloud)")
        GENERATE_NARRATION = "GENERATE_NARRATION", _("Generate Narration (Cloud)")
        GENERATE_EDITING_SCRIPT = "GENERATE_EDITING_SCRIPT", _("Generate Editing Script (Cloud)")
        RUN_DUBBING = "RUN_DUBBING", _("Run Dubbing (Edge)")
        RUN_SYNTHESIS = "RUN_SYNTHESIS", _("Run Synthesis (Edge)")

    class TaskStatus(models.TextChoices):
        PENDING = "PENDING", _("Pending")
        ASSIGNED = "ASSIGNED", _("Assigned")
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
    task_type = TextChoicesField(
        choices_enum=TaskType,
        max_length=30,
        verbose_name=_("Task Type")
    )
    status = FSMField(
        default=TaskStatus.PENDING,
        choices=TaskStatus.choices,
        max_length=10,
        verbose_name=_("Status")
    )
    payload = models.JSONField(_("Payload"), default=dict, blank=True)
    result = models.JSONField(_("Result"), default=dict, blank=True, null=True)
    logs = models.TextField(_("Logs"), blank=True, null=True)

    class Meta:
        verbose_name = _("Task")
        verbose_name_plural = _("Tasks")
        ordering = ['-created']

    def __str__(self):
        return f"Task {self.id} ({self.task_type}) - {self.status}"

    # --- 状态机转换逻辑 ---
    @transition(field=status, source=TaskStatus.PENDING, target=TaskStatus.ASSIGNED)
    def assign_to_edge(self, edge_instance):
        self.assigned_edge = edge_instance

    @transition(field=status, source=TaskStatus.ASSIGNED, target=TaskStatus.RUNNING)
    def start(self):
        pass

    @transition(field=status, source=[TaskStatus.RUNNING, TaskStatus.ASSIGNED], target=TaskStatus.COMPLETED)
    def complete(self, result_data):
        self.result = result_data

    @transition(field=status, source='*', target=TaskStatus.FAILED)
    def fail(self, error_message):
        self.result = {"error": error_message}