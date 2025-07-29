from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

# API Router
router = DefaultRouter()

urlpatterns = [
    # Web Views
    path('', views.home, name='home'),
    path('manager/', views.manager_dashboard, name='manager_dashboard'),
    path('agent/', views.agent_dashboard, name='agent_dashboard'),
    path('upload-clients/', views.upload_clients, name='upload_clients'),

    # API Endpoints
    path('api/', include(router.urls)),
    path('api/auto-assign/', views.auto_assign_client, name='auto_assign_client'),
    path('api/assignment/<uuid:assignment_id>/status/', views.update_assignment_status, name='update_assignment_status'),
    path('api/location/update/', views.update_agent_location, name='update_agent_location'),
    path('api/route/', views.get_route, name='get_route'),
]
