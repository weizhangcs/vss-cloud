# core/permissions.py
from rest_framework.permissions import BasePermission

class IsEdgeAuthenticated(BasePermission):
    """
    自定义权限：允许通过 EdgeInstanceAuthentication 认证的请求。
    逻辑：只要 request.auth 不为空（即 EdgeInstance 已验证），就放行。
    """
    def has_permission(self, request, view):
        # EdgeInstanceAuthentication 成功时会返回 (None, edge_instance)
        # 所以 request.auth 就是 edge_instance 对象
        return bool(request.auth)