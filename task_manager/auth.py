# task_manager/auth.py
import uuid
from django.http import HttpRequest
from ninja.security import APIKeyHeader
from organization.models import EdgeInstance


class EdgeAuth(APIKeyHeader):
    """
    边缘实例鉴权 (适配 EdgeInstance 模型)
    Headers:
      X-Instance-ID: <instance_id (UUID)>
      X-Api-Key:     <api_key (UUID)>
    """
    # 1. 定义主键 Header，Ninja 会自动提取这个 Header 的值传给 authenticate 的第二个参数
    param_name = "X-Instance-ID"

    def authenticate(self, request: HttpRequest, instance_id_str: str):
        # 2. 获取辅助校验 Header (API Key)
        api_key_str = request.headers.get("X-Api-Key")

        if not instance_id_str or not api_key_str:
            return None

        try:
            # 3. 校验 UUID 格式 (防止非法字符串导致数据库查询报错)
            # 虽然 Django ORM 有一定的容错，但显式转换更安全
            try:
                valid_instance_uuid = uuid.UUID(instance_id_str)
            except ValueError:
                return None

            # 4. 数据库查询
            instance = EdgeInstance.objects.get(instance_id=valid_instance_uuid)

            # 5. 核心逻辑校验
            # A. 校验 API Key (注意：数据库里的 api_key 是 UUID 对象，需要转字符串比对)
            # B. 校验 实例是否启用 (is_enabled)
            # C. 校验 状态是否处于维护中 (可选，取决于你的业务需求，这里暂时只卡 is_enabled)
            if str(instance.api_key) == api_key_str and instance.is_enabled:
                request.auth = instance  # 将实例挂载到 request 上
                return instance

        except EdgeInstance.DoesNotExist:
            # 查无此人
            pass
        except Exception:
            # 捕获其他潜在异常，防止 500
            pass

        return None