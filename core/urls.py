"""
URL configuration for core project.
"""
from django.urls import path, include

urlpatterns = [
    path('', include('gatekeeper.urls')),
]
