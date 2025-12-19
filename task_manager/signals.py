# task_manager/signals.py
from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Task
from .tasks import execute_cloud_native_task

# --- [新增] 1. 定义路由映射表 ---
# 这里的 Key 必须与 Task.TaskType 定义完全一致
# Value 是我们在 Celery 中定义的物理队列名称
QUEUE_ROUTING = {
    # === A类: Gemini API 密集型 (低并发，防429) ===
    # 特点: 强依赖 Google Vertex AI，配额敏感
    Task.TaskType.GENERATE_NARRATION: 'queue_gemini',
    Task.TaskType.LOCALIZE_NARRATION: 'queue_gemini',
    Task.TaskType.CHARACTER_IDENTIFIER: 'queue_gemini',
    Task.TaskType.VISUAL_ANALYSIS: 'queue_gemini',
    # B-Roll 选择主要使用 Gemini 进行语义分析
    Task.TaskType.GENERATE_EDITING_SCRIPT: 'queue_gemini',
    Task.TaskType.SUBTITLE_CONTEXT: 'queue_gemini',

    # === B类: 音频/计算密集型 (中等并发) ===
    # 特点: 涉及 Aliyun CosyVoice (PAI-EAS) 或 Google TTS
    # 虽然 Google TTS 配额较高，但音频处理本身不仅耗 IO 还耗 CPU (编解码)
    Task.TaskType.GENERATE_DUBBING: 'queue_audio',

    # === C类: IO 密集型/运维类 (高并发) ===
    # 特点: 主要是 GCS 文件上传下载、数据库读写，不易触发 API 限制
    Task.TaskType.DEPLOY_RAG_CORPUS: 'queue_io',
}


@receiver(post_save, sender=Task)
def trigger_task_execution(sender, instance, created, **kwargs):
    """
    当一个新的 Task 实例的保存事务 *成功提交后*，触发异步任务。
    """
    if created:
        # 我们只处理定义的云原生任务类型
        # 如果是 Edge 端执行的任务 (如 RUN_DUBBING)，这里通常不处理，或者分发给 Edge 队列(未来实现)

        # 获取目标队列，如果未定义则回退到默认的 'celery' 队列
        target_queue = QUEUE_ROUTING.get(instance.task_type, 'celery')

        # [日志] 方便调试路由逻辑 (生产环境可调整日志级别)
        # print(f"New cloud-native task saved (ID: {instance.id}). Scheduling to queue: {target_queue}")

        # 2. 将 .apply_async() 调用包裹在 transaction.on_commit 中
        # 这确保了只有在 Task 记录确实已经存在于数据库中之后，
        # Celery 任务才会被发送到队列里。
        transaction.on_commit(
            lambda: execute_cloud_native_task.apply_async(
                args=[instance.id],
                queue=target_queue  # <--- [核心修改] 指定物理队列
            )
        )