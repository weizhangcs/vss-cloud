# task_manager/admin.py

import json
from django.contrib import admin
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.utils.html import format_html
from .models import Task

from unfold.admin import ModelAdmin
from unfold.decorators import display


@admin.register(Task)
class TaskAdmin(ModelAdmin):
    list_display = (
        'id',
        'task_type_label',
        'status_badge',
        'organization',
        'assigned_edge',
        'created_at_formatted',
        'modified_at_formatted'
    )

    list_display_links = ('id', 'task_type_label', 'status_badge', 'organization')

    list_filter = (
        'status',
        'task_type',
        'organization',
        'assigned_edge',
        'created'
    )

    list_per_page = 20
    search_fields = ('id', 'organization__name', 'assigned_edge__name')
    ordering = ['-created']

    fieldsets = (
        (_("Basic Info"), {
            "fields": (
                ("task_type", "organization"),
                ("status", "assigned_edge"),
            )
        }),
        (_("Performance & Timing"), {  # [新增] 性能统计区域
            "fields": (
                ("created", "started_at"),
                ("finished_at", "duration"),
            )
        }),
        (_("Data Inspector"), {
            "classes": ("collapse", "open"),
            "description": _("Input payload, execution result and system logs."),
            "fields": (
                "payload_pretty",
                "result_pretty",
                "logs"
            )
        }),
    )

    # [新增] 将新字段加入只读列表
    readonly_fields = (
        'payload_pretty',
        'result_pretty',
        'created',
        'modified',
        'logs',
        'started_at',
        'finished_at',
        'duration'
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return True

    @display(description=_("Status"), label={
        Task.TaskStatus.PENDING: "info",
        # [修改] 移除 ASSIGNED 映射
        Task.TaskStatus.RUNNING: "warning",
        Task.TaskStatus.COMPLETED: "success",
        Task.TaskStatus.FAILED: "danger",
    })
    def status_badge(self, obj):
        return obj.status

    @display(description=_("Task Type"))
    def task_type_label(self, obj):
        return obj.get_task_type_display()

    @display(description=_("Created"))
    def created_at_formatted(self, obj):
        # [修复] 先转为本地时间 (Shanghai)，再格式化
        local_dt = timezone.localtime(obj.created)
        return local_dt.strftime("%Y-%m-%d %H:%M")

    @display(description=_("Modified"))
    def modified_at_formatted(self, obj):
        # [修复] 先转为本地时间 (Shanghai)，再格式化
        local_dt = timezone.localtime(obj.modified)
        return local_dt.strftime("%Y-%m-%d %H:%M")

    def payload_pretty(self, obj):
        if not obj.payload:
            return "-"
        json_str = json.dumps(obj.payload, indent=2, ensure_ascii=False)
        return format_html(
            '<pre class="w-full" style="background-color: #f8f9fa; padding: 15px; border-radius: 5px; font-size: 12px; line-height: 1.5; overflow-x: auto; border: 1px solid #e2e8f0;">{}</pre>',
            json_str
        )

    payload_pretty.short_description = _("Payload")

    def result_pretty(self, obj):
        if not obj.result:
            return "-"
        json_str = json.dumps(obj.result, indent=2, ensure_ascii=False)
        bg_color = "#fff5f5" if obj.status == Task.TaskStatus.FAILED else "#f0fdf4"
        border_color = "#fed7d7" if obj.status == Task.TaskStatus.FAILED else "#c6f6d5"

        return format_html(
            '<pre class="w-full" style="background-color: {}; padding: 15px; border-radius: 5px; font-size: 12px; line-height: 1.5; overflow-x: auto; border: 1px solid {};">{}</pre>',
            bg_color, border_color, json_str
        )

    result_pretty.short_description = _("Result")