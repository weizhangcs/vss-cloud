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
from ninja import NinjaAPI
from task_manager.api import router as task_router
from file_service.api import router as file_router

api = NinjaAPI(
    title="VSS Cloud API",
    version="1.3.0",
    description="Visify Story Studio Cloud Native API",
    urls_namespace="vss_api"
)

api.add_router("/tasks", task_router, tags=["Task Manager"])
api.add_router("/files", file_router, tags=["File Service"])

urlpatterns = [
    path("i18n/", include("django.conf.urls.i18n")),  #
    path("api/v1/", api.urls),
]

# --- [核心修改] ---
# 只有面向用户的页面 (Admin) 才应该在 i18n_patterns 中
urlpatterns += i18n_patterns(
    path("admin/", admin.site.urls),  #
)