# organization/admin.py

from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from pydantic import ValidationError

from .models import Organization, UserProfile, EdgeInstance
from django import forms
# 引入 Unfold 的组件
from django.db.models import Count
from unfold.admin import ModelAdmin
from unfold.widgets import UnfoldAdminTextInputWidget, UnfoldAdminSelectWidget, UnfoldAdminTextareaWidget, \
    UnfoldAdminEmailInputWidget, UnfoldAdminPasswordInput
from django.utils.translation import gettext_lazy as _


# ... (UserProfileInline, UserAdmin, UserProfileAddForm, UserProfileAdmin 保持不变) ...
# ... (请保留原文件中关于 UserProfile 的部分) ...

# -----------------------------------------------------------------
# Organization Admin
# -----------------------------------------------------------------

@admin.register(Organization)
class OrganizationAdmin(ModelAdmin):
    list_display = ('name', 'attribute', 'business_status', 'edge_instance_count', 'contact_name', 'created')
    list_filter = ('business_status', 'attribute')
    search_fields = ('name', 'contact_name', 'contact_email')

    fieldsets = (
        (_("Basic Info"), {
            "fields": (
                # 第一行：3个字段 1:1:1
                ("name", "attribute", "business_status"),
                # 第二行：备注 (上下布局)
                "description"
            )
        }),
        (_("Contact Details"), {
            "fields": (
                # 第一行：姓名 : 职务
                ("contact_name", "contact_position"),
                # 第二行：Email : 电话
                ("contact_email", "contact_phone"),
            ),
            "classes": ("collapse",),
        }),
        (_("System Info"), {
            "fields": ("org_id", "created", "modified"),
            "classes": ("collapse",),
        }),
    )
    readonly_fields = ('org_id', 'created', 'modified')

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.annotate(edge_count=Count('edge_instances'))

    def edge_instance_count(self, obj):
        return obj.edge_count

    edge_instance_count.short_description = _("Edge Count")
    edge_instance_count.admin_order_field = 'edge_count'


# -----------------------------------------------------------------
# EdgeInstance Admin
# -----------------------------------------------------------------

@admin.register(EdgeInstance)
class EdgeInstanceAdmin(ModelAdmin):
    list_display = ('name', 'organization', 'status', 'is_enabled', 'deployment_type', 'software_version')
    list_filter = ('is_enabled', 'status', 'deployment_type', 'organization', 'software_version')
    search_fields = ('name', 'organization__name', 'instance_id')

    fieldsets = (
        (_("Identity"), {
            "fields": (
                # 第一行：Name : Org
                ("name", "organization"),
                # 第二行：Conn Status : Biz Enabled
                ("status", "is_enabled")
            )
        }),
        (_("Credentials"), {
            "fields": (
                # Instance ID : API Key
                ("instance_id", "api_key"),
            ),
            "classes": ("collapse",),
        }),
        (_("Asset Ledger"), {
            "description": _("Static deployment information."),
            "classes": ("collapse",),
            "fields": (
                # Deployment Type : Version
                ("deployment_type", "software_version"),
                # Remarks
                "description"
            )
        }),
        (_("Telemetry"), {
            "fields": (
                # Created : Modified : Last Heartbeat
                ("created", "modified", "last_heartbeat"),
            ),
            "classes": ("collapse",),
        }),
    )

    readonly_fields = ('instance_id', 'last_heartbeat', 'created', 'modified')

# --- UserProfileInline 和 UserAdmin (保持不变) ---
class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    verbose_name_plural = _('Profile')  # <-- i18n
    fk_name = 'user'


class UserAdmin(BaseUserAdmin):
    inlines = (UserProfileInline,)
    list_display = ('username', 'email', 'first_name', 'last_name', 'is_staff')

    def get_inline_instances(self, request, obj=None):
        if not obj:
            return list()
        return super().get_inline_instances(request, obj)


admin.site.unregister(User)
admin.site.register(User, UserAdmin)

# -----------------------------------------------------------------
# UserProfileAdmin (应用 Unfold Widgets 和 i18n)
# -----------------------------------------------------------------

class UserProfileAddForm(forms.ModelForm):
    username = forms.CharField(
        label=_("Username"),  # <-- i18n
        widget=UnfoldAdminTextInputWidget(),
        max_length=150,
        required=True,
        help_text=_("Required. 150 characters or fewer. Letters, digits and @/./+/-/_ only.")  # <-- i18n
    )
    email = forms.EmailField(
        label=_("Email address"),  # <-- i18n
        widget=UnfoldAdminEmailInputWidget(),
        required=False,
        help_text=_("Optional.")  # <-- i18n
    )
    password = forms.CharField(
        label=_("Password"),  # <-- i18n
        widget=UnfoldAdminPasswordInput(),
        required=True
    )
    password_confirm = forms.CharField(
        label=_("Confirm Password"),  # <-- i18n
        widget=UnfoldAdminPasswordInput(),
        required=True
    )

    class Meta:
        model = UserProfile
        fields = ('organization', 'role')

    def clean_password_confirm(self):
        password = self.cleaned_data.get("password")
        password_confirm = self.cleaned_data.get("password_confirm")
        if password and password_confirm and password != password_confirm:
            raise ValidationError(_("Passwords do not match."))  # <-- i18n
        return password_confirm

    def clean_username(self):
        username = self.cleaned_data.get("username")
        if User.objects.filter(username=username).exists():
            raise ValidationError(_("A user with this username already exists."))  # <-- i18n
        return username

@admin.register(UserProfile)
class UserProfileAdmin(ModelAdmin):
    list_display = ('user', 'organization', 'role')
    list_filter = ('organization', 'role')
    search_fields = ('user__username', 'organization__name')
    raw_id_fields = ('organization', 'user')
    add_form = UserProfileAddForm

    def get_fields(self, request, obj=None):
        if obj is None:
            return ('username', 'email', 'password', 'password_confirm', 'organization', 'role')
        return ('user', 'organization', 'role')

    def get_readonly_fields(self, request, obj=None):
        if obj:
            return ('user',)
        return ()

    def get_form(self, request, obj=None, **kwargs):
        defaults = {}
        if obj is None:
            defaults['form'] = self.add_form
        defaults.update(kwargs)
        return super().get_form(request, obj, **defaults)

    def save_model(self, request, obj, form, change):
        if not change:
            try:
                user = User.objects.create_user(
                    username=form.cleaned_data['username'],
                    email=form.cleaned_data['email'],
                    password=form.cleaned_data['password']
                )
                user.is_staff = False
                user.is_superuser = False
                user.save()
                obj.user = user
            except Exception as e:
                messages.error(request, _(f"Could not create user: {e}"))  # <-- i18n
                return
        super().save_model(request, obj, form, change)