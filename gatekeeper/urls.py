from django.urls import path
from . import views

urlpatterns = [
    path('', views.sheet_dashboard_view, name='sheet_dashboard'),
    path('auth/login/', views.discord_login, name='discord_login'),
    path('auth/discord/callback/', views.discord_callback, name='discord_callback'),
    path('auth/logout/', views.discord_logout, name='discord_logout'),
    path('access-denied/', views.access_denied, name='access_denied'),
    path('api/update-cell/', views.update_cell_api, name='api_update_cell'),
    path('api/add-row/', views.add_row_api, name='api_add_row'),
]
