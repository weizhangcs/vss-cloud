# organization/admin.py
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from .models import Organization, UserProfile, EdgeInstance

from django import forms
from django.core.exceptions import ValidationError
from django.contrib import messages

# --- 导入 Unfold Admin ---
from unfold.admin import ModelAdmin

# --- 导入 Unfold Widgets ---
from unfold.widgets import (
    UnfoldAdminTextInputWidget,
    UnfoldAdminEmailInputWidget,
    UnfoldAdminPasswordInput,  # 使用您修正后的正确名称
    UnfoldAdminSelectWidget
)

# --- 导入 i18n 翻译 ---
from django.utils.translation import gettext_lazy as _


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
# Organization Admin (应用 Unfold Widgets 和 i18n)
# -----------------------------------------------------------------

class OrganizationAddForm(forms.ModelForm):
    name = forms.CharField(
        label=_("Organization Name"),  # <-- i18n
        widget=UnfoldAdminTextInputWidget(),
        required=True
    )

    class Meta:
        model = Organization
        fields = ('name',)


@admin.register(Organization)
class OrganizationAdmin(ModelAdmin):
    list_display = ('name', 'created')
    search_fields = ('name',)

    add_form = OrganizationAddForm

    def get_form(self, request, obj=None, **kwargs):
        defaults = {}
        if obj is None:
            defaults['form'] = self.add_form
        defaults.update(kwargs)
        return super().get_form(request, obj, **defaults)


# -----------------------------------------------------------------
# EdgeInstance Admin (应用 Unfold Widgets 和 i18n)
# -----------------------------------------------------------------

class EdgeInstanceAddForm(forms.ModelForm):
    name = forms.CharField(
        label=_("Instance Name"),  # <-- i18n
        widget=UnfoldAdminTextInputWidget(),
        required=True
    )
    status = forms.ChoiceField(
        label=_("Status"),  # <-- i18n
        widget=UnfoldAdminSelectWidget(),
        choices=EdgeInstance.EdgeStatus.choices,
        initial=EdgeInstance.EdgeStatus.OFFLINE,
        required=True
    )

    class Meta:
        model = EdgeInstance
        fields = ('name', 'organization', 'status')


@admin.register(EdgeInstance)
class EdgeInstanceAdmin(ModelAdmin):
    list_display = ('name', 'organization', 'status', 'instance_id', 'last_heartbeat')
    list_filter = ('status', 'organization')
    search_fields = ('name', 'organization__name', 'instance_id')

    raw_id_fields = ('organization',)
    add_form = EdgeInstanceAddForm

    def get_form(self, request, obj=None, **kwargs):
        defaults = {}
        if obj is None:
            defaults['form'] = self.add_form
        defaults.update(kwargs)
        return super().get_form(request, obj, **defaults)

    def get_fields(self, request, obj=None):
        if obj:
            return ('name', 'organization', 'status', 'instance_id', 'api_key', 'last_heartbeat')
        else:
            # 'organization' 字段由 raw_id_fields 自动处理
            return ('name', 'organization', 'status')

    def get_readonly_fields(self, request, obj=None):
        if obj:
            return ('instance_id', 'api_key', 'last_heartbeat', 'created', 'modified')
        else:
            return ('created', 'modified')


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