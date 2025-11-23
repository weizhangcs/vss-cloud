# organization/models.py
import uuid
from django.db import models
from django.contrib.auth.models import User
from django.utils.translation import gettext_lazy as _
from django.db.models.signals import post_save
from django.dispatch import receiver

# model_utils 和 django-choices 已经在 requirements.txt 中
from model_utils.models import TimeStampedModel
from django_choices_field import TextChoicesField


class Organization(TimeStampedModel):
    """
    租户（组织）模型。
    """
    # [新增] 组织唯一标识符 (不可变)
    # 使用 UUID 确保全局唯一性，且不会随名称变更而改变
    org_id = models.UUIDField(
        _("Organization ID"),
        default=uuid.uuid4,
        editable=False,
        unique=True #如果是开发环境：建议直接删除数据库重建，或者删除旧的 migration 文件重新 makemigrations;如果是生产环境：需要先添加 null=True，通过脚本填充 UUID，然后再改为 unique=True, null=False
    )

    name = models.CharField(_("Organization Name"), max_length=255, unique=True)

    class Meta:
        verbose_name = _("Organization")
        verbose_name_plural = _("Organizations")
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({str(self.org_id)[:8]})"


class UserProfile(TimeStampedModel):
    """
    扩展的 User 模型，用于关联租户和角色。
    """
    class Role(models.TextChoices):
        OWNER = "OWNER", _("Owner")
        ADMIN = "ADMIN", _("Admin")
        MEMBER = "MEMBER", _("Member")

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="profile",
        verbose_name=_("User")
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="members",
        verbose_name=_("Organization"),
        null = True,  # <-- 允许数据库中该字段为空
        blank = True  # <-- 允许 Django Admin 中该字段为空
        # 警告：此字段在数据库中是必需的 (NOT NULL)
        # 这与您的 signals.py 中的 create_user_profile 逻辑有冲突
    )
    role = TextChoicesField(
        choices_enum=Role,
        default=Role.MEMBER,
        max_length=10,
        verbose_name=_("Role")
    )

    class Meta:
        verbose_name = _("User Profile")
        verbose_name_plural = _("User Profiles")

    def __str__(self):
        return f"{self.user.username} ({self.organization.name})"

class EdgeInstance(TimeStampedModel):
    """
    边缘实例模型。
    代表一个已向云端注册的 visify-ssw (边缘) 实例。
    [已从 task_manager.models 移至此处]
    """

    class EdgeStatus(models.TextChoices):
        ONLINE = "ONLINE", _("Online")
        OFFLINE = "OFFLINE", _("Offline")

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="edge_instances",
        verbose_name=_("Organization")
    )
    instance_id = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
        verbose_name=_("Instance ID")
    )
    api_key = models.UUIDField(
        default=uuid.uuid4,
        editable=True,  # 保持 True，以便 Admin 可以重置
        unique=True,
        verbose_name=_("API Key")
    )
    name = models.CharField(_("Instance Name"), max_length=255)
    status = TextChoicesField(
        choices_enum=EdgeStatus,
        default=EdgeStatus.OFFLINE,
        max_length=10,
        verbose_name=_("Status")
    )
    last_heartbeat = models.DateTimeField(
        _("Last Heartbeat"),
        null=True,
        blank=True
    )

    class Meta:
        verbose_name = _("Edge Instance")
        verbose_name_plural = _("Edge Instances")
        unique_together = ('organization', 'name')

    def __str__(self):
        return f"{self.name} ({self.organization.name})"