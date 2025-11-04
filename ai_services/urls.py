from django.urls import path
from .views import CharacterIdentifierAPIView

urlpatterns = [
    path('character/identify/', CharacterIdentifierAPIView.as_view(), name='character-identify'),
]