# organization/models.py

import uuid
from django.db import models
from django.contrib.auth.models import User
from django.utils.translation import gettext_lazy as _
from model_utils.models import TimeStampedModel
from django_choices_field import TextChoicesField


class Organization(TimeStampedModel):
    """
    租户（组织）模型。
    v1.2 扩充：增加业务属性、商业状态及详细联系人信息。
    """

    class OrgAttribute(models.TextChoices):
        PERSONAL = "PERSONAL", _("Personal")
        COMPANY = "COMPANY", _("Company")
        COMMUNITY = "COMMUNITY", _("Community")

    class BusinessStatus(models.TextChoices):
        INTENTION = "INTENTION", _("Intention")
        SIGNED = "SIGNED", _("Signed")
        SUSPENDED = "SUSPENDED", _("Suspended")
        PARTNER = "PARTNER", _("Partner")
        INTERNAL = "INTERNAL", _("Group Internal")

    org_id = models.UUIDField(
        _("Organization ID"),
        default=uuid.uuid4,
        editable=False,
        unique=True
    )
    name = models.CharField(_("Organization Name"), max_length=255, unique=True)

    # --- 业务台账信息 ---
    attribute = TextChoicesField(
        choices_enum=OrgAttribute,
        default=OrgAttribute.COMPANY,
        verbose_name=_("Attribute"),
        help_text=_("The nature of the organization.")
    )

    business_status = TextChoicesField(
        choices_enum=BusinessStatus,
        default=BusinessStatus.INTENTION,
        verbose_name=_("Business Status"),
        help_text=_("Current business relationship status.")
    )

    # --- 运营联系信息 ---
    contact_name = models.CharField(_("Contact Name"), max_length=100, blank=True)
    # [新增] 联系人职务
    contact_position = models.CharField(_("Contact Position"), max_length=100, blank=True)

    contact_email = models.EmailField(_("Contact Email"), blank=True)
    # [新增] 联系电话
    contact_phone = models.CharField(_("Contact Phone"), max_length=20, blank=True)

    description = models.TextField(_("Description/Notes"), blank=True, help_text=_("Internal notes."))

    class Meta:
        verbose_name = _("Organization")
        verbose_name_plural = _("Organizations")
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.get_business_status_display()})"


class UserProfile(TimeStampedModel):
    # ... (保持不变) ...
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
        null=True,
        blank=True
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
    """

    class EdgeStatus(models.TextChoices):
        ONLINE = "ONLINE", _("Online")
        OFFLINE = "OFFLINE", _("Offline")
        MAINTENANCE = "MAINTENANCE", _("Maintenance")

    class DeploymentType(models.TextChoices):
        WIN_DOCKER = "WIN_DOCKER", _("Windows Docker Desktop (Local/LAN)")
        LINUX_DOCKER = "LINUX_DOCKER", _("Linux Docker")
        WORKSTATION = "WORKSTATION", _("All-in-One Workstation")

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
        editable=True,
        unique=True,
        verbose_name=_("API Key")
    )
    name = models.CharField(_("Instance Name"), max_length=255)

    status = TextChoicesField(
        choices_enum=EdgeStatus,
        default=EdgeStatus.OFFLINE,
        max_length=15,
        verbose_name=_("Connection Status")
    )

    is_enabled = models.BooleanField(
        _("Is Enabled"),
        default=True,
        help_text=_("Business switch to enable/disable this instance.")
    )

    deployment_type = TextChoicesField(
        choices_enum=DeploymentType,
        default=DeploymentType.LINUX_DOCKER,
        verbose_name=_("Deployment Type")
    )

    software_version = models.CharField(_("Software Version"), max_length=50, blank=True, default="v1.0.0")
    description = models.TextField(_("Remarks"), blank=True)

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
        state_mark = "✅" if self.is_enabled else "🚫"
        return f"{state_mark} {self.name}"