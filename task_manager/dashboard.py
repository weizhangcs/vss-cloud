# task_manager/dashboard.py

from datetime import timedelta
from django.utils import timezone
from django.db.models import Count, Sum, Q
from django.db.models.functions import TruncDay
from django.utils.translation import gettext_lazy as _


def dashboard_callback(request, context):
    from .models import Task
    from organization.models import EdgeInstance

    # [关键修复] 获取当前的【本地时间】(Asia/Shanghai)，而不是 UTC
    now = timezone.localtime()

    # --- 1. 设定时间窗口 (过去7天) ---
    days_range = 7

    # 计算起始时间：从当前时间往前推7天
    # 我们将起始时间设为那那一天的 00:00:00，确保覆盖完整的一天
    start_date = (now - timedelta(days=days_range - 1)).replace(hour=0, minute=0, second=0, microsecond=0)

    recent_tasks = Task.objects.filter(created__gte=start_date)

    # --- 2. 计算 KPI ---
    total_count = recent_tasks.count()
    failed_count = recent_tasks.filter(status=Task.TaskStatus.FAILED).count()

    success_rate = 0
    if total_count > 0:
        success_count = recent_tasks.filter(status=Task.TaskStatus.COMPLETED).count()
        success_rate = round((success_count / total_count) * 100, 1)

    duration_sum = recent_tasks.aggregate(total=Sum('duration'))['total']
    total_hours = 0.0
    if duration_sum:
        total_hours = round(duration_sum.total_seconds() / 3600, 2)

    active_edges = EdgeInstance.objects.filter(status=EdgeInstance.EdgeStatus.ONLINE).count()
    total_edges = EdgeInstance.objects.count()

    # --- 3. 构建图表数据 ---
    # TruncDay 会自动遵循 settings.TIME_ZONE (Asia/Shanghai) 进行截断
    daily_stats = (
        recent_tasks
        .annotate(day=TruncDay('created'))
        .values('day')
        .annotate(
            total=Count('id'),
            success=Count('id', filter=Q(status=Task.TaskStatus.COMPLETED)),
            failed=Count('id', filter=Q(status=Task.TaskStatus.FAILED))
        )
        .order_by('day')
    )

    chart_labels = []
    data_total = []
    data_success = []

    # 将查询结果转为字典方便查找
    # stat['day'] 已经是本地日期的 datetime 对象了
    stats_dict = {stat['day'].date(): stat for stat in daily_stats}

    # 循环填充数据（确保没有数据的日期显示为0）
    for i in range(days_range):
        # 使用本地时间的 start_date 进行推算
        date_cursor = (start_date + timedelta(days=i)).date()

        stat = stats_dict.get(date_cursor, {'total': 0, 'success': 0})

        chart_labels.append(date_cursor.strftime("%m-%d"))
        data_total.append(stat['total'])
        data_success.append(stat['success'])

    # --- 4. 更新上下文 ---
    context.update({
        "kpi": [
            {
                "title": _("Tasks (7 Days)"),
                "value": total_count,
                "footer": _("Total tasks processed"),
            },
            {
                "title": _("Success Rate"),
                "value": f"{success_rate}%",
                "footer": f"{failed_count} failed tasks",
            },
            {
                "title": _("Compute Hours"),
                "value": f"{total_hours} h",
                "footer": _("Cumulative duration"),
            },
            {
                "title": _("Active Edges"),
                "value": f"{active_edges} / {total_edges}",
                "footer": _("Online instances"),
            },
        ],
        "chart": {
            "labels": chart_labels,
            "datasets": [
                {
                    "label": "Total Tasks",
                    "data": data_total,
                    "borderColor": "#9333ea",
                    "backgroundColor": "rgba(147, 51, 234, 0.1)",
                },
                {
                    "label": "Success",
                    "data": data_success,
                    "borderColor": "#10b981",
                    "backgroundColor": "rgba(16, 185, 129, 0.1)",
                }
            ]
        }
    })

    return context