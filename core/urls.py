"""
URL configuration for core project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from django.conf.urls.i18n import i18n_patterns

urlpatterns = [
    # 语言切换器视图 (保持不变，无前缀)
    path("i18n/", include("django.conf.urls.i18n")),  #

    # --- [核心修改] ---
    # 将所有 API 移到 i18n_patterns 之外
    # 它们将不再有 /en/ 前缀，也不会触发重定向
    path('api/v1/files/', include('file_service.urls', namespace='file_service')),
    path('api/v1/tasks/', include('task_manager.urls', namespace='task_manager')),
    # ------------------
]

# --- [核心修改] ---
# 只有面向用户的页面 (Admin) 才应该在 i18n_patterns 中
urlpatterns += i18n_patterns(
    path("admin/", admin.site.urls),  #

    # [移除] path('api/v1/files/', ...)
    # [移除] path('api/v1/tasks/', ...)
)