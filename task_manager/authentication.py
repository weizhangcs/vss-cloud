# task_manager/authentication.py
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from organization.models import EdgeInstance

class EdgeInstanceAuthentication(BaseAuthentication):
    """
    自定义认证类，用于验证来自边缘实例的请求。
    它会检查请求头中的 'X-Instance-ID' 和 'X-Api-Key'。
    """
    def authenticate(self, request):
        instance_id = request.headers.get('X-Instance-ID')
        api_key = request.headers.get('X-Api-Key')

        if not instance_id or not api_key:
            # 如果请求头中没有提供凭证，则认证失败
            return None

        try:
            # 尝试根据凭证查找一个在线的 EdgeInstance
            edge_instance = EdgeInstance.objects.get(
                instance_id=instance_id,
                api_key=api_key,
                # 我们也可以添加状态检查，例如只允许 ONLINE 的实例
                # status=EdgeInstance.EdgeStatus.ONLINE
            )
        except EdgeInstance.DoesNotExist:
            raise AuthenticationFailed('Invalid instance_id or api_key.')

        # 认证成功后，我们将 edge_instance 对象附加到 request 对象上，
        # 这样后续的 View 就可以直接使用它了。
        # BaseAuthentication 要求返回一个 (user, auth) 的元组。
        # 在这种机器对机器的认证中，'user' 可能不是必须的，
        # 但我们可以返回该实例所属组织的任意一个用户，以兼容Django的权限系统。
        # 为了简化，我们暂时返回 (None, edge_instance)。
        return (None, edge_instance)

    def authenticate_header(self, request):
        # 当认证失败时，在 WWW-Authenticate 响应头中返回的方案
        return 'APIKey'