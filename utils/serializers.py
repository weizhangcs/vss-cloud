from rest_framework import serializers

class FileUploadSerializer(serializers.Serializer):
    """
    用于验证文件上传的简单 Serializer。
    """
    # 'file' 是 Edge 客户端在 multipart/form-data 中
    # 必须提供的字段名称。
    file = serializers.FileField(required=True, write_only=True)