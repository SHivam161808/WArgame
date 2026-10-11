from django.urls import path

from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('console/', views.console, name='console'),
    path('console/scenario/<str:sid>/', views.scenario_setup, name='scenario'),
    path('console/scenario/<str:sid>/live/', views.live, name='live'),
    path('console/assets/', views.assets, name='assets'),
    path('console/orbat/', views.orbat, name='orbat'),
    path('play/<str:code>/', views.play, name='play'),
    path('aar/<str:bid>/', views.aar, name='aar'),
]
