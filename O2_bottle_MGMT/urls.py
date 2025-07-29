"""
URL configuration for O2_bottle_MGMT project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path
from django.conf import settings
from django.conf.urls.static import static
from bottle_MGMT import views

urlpatterns = [
    path('', views.login_view, name='login'),
    path('admin-dashboard/', views.admin_dashboard, name='admin_dashboard'),
    path('delivery-dashboard/', views.delivery_dashboard, name='delivery_dashboard'),
    path('clients/', views.client_list, name='client_list'),
    path('clients/create/', views.client_create, name='client_create'),
    path('transactions/', views.transaction_list, name='transaction_list'),
    path('transactions/create/', views.transaction_create, name='transaction_create'),
    path('reports/', views.reports_view, name='reports'),
    path('inventory/', views.inventory_view, name='inventory'),
    path('inventory/add-bottles/', views.add_bottles_view, name='add_bottles'),
    path('inventory/bottle/<str:code>/photos/', views.bottle_photos_view, name='bottle_photos'),
    path('debug-photos/', views.debug_photos, name='debug_photos'),
    path('pricing/', views.pricing_view, name='pricing'),
    path('logout/', views.logout_view, name='logout'),
    path('clients/<int:client_id>/bill/', views.generate_bill, name='generate_bill'),
    path('clients/<int:client_id>/bill/<int:bill_id>/', views.generate_bill, name='generate_bill'),
    path('clients/<int:client_id>/custom-billing/', views.custom_billing_view, name='custom_billing'),
    path('clients/<int:client_id>/create-custom-bill/', views.create_custom_bill, name='create_custom_bill'),
    path('clients/<int:client_id>/bill-history/', views.bill_history, name='bill_history'),
    path('bills/<int:bill_id>/mark-paid/', views.mark_bill_paid, name='mark_bill_paid'),
    path('bills/<int:bill_id>/delete/', views.delete_bill, name='delete_bill'),
    path('sales/', views.sales_analytics, name='sales_analytics'),
]

# Serve media files in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
