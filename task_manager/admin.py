# task_manager/admin.py
from django.contrib import admin
#from fsm_admin.mixins import FSMTransitionMixin
from .models import Task

@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):  # <-- 3. 只继承 admin.ModelAdmin
    list_display = ('id', 'task_type', 'organization', 'status', 'assigned_edge', 'created')
    list_filter = ('status', 'task_type', 'organization')
    search_fields = ('id', 'organization__name', 'assigned_edge__name')

    # 4. 在新建页面(add view)，所有字段都应该是可编辑的
    #    在修改页面(change view)，'status' 应该是只读的
    def get_readonly_fields(self, request, obj=None):
        if obj:  # obj is not None, so this is a change view
            return ['status', 'created', 'modified']
        else:  # obj is None, so this is an add view
            return ['created', 'modified']