"""
URL configuration for chaviprom project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
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
from django.views.generic import TemplateView
from django.conf.urls.i18n import i18n_patterns
from django.contrib.auth import views as auth_views
from django.conf import settings
from django.conf.urls.static import static
from two_factor.urls import urlpatterns as tf_urls

urlpatterns = [
    path('i18n/', include('django.conf.urls.i18n')),
]

urlpatterns += i18n_patterns(
    path('',include(tf_urls)),
    path('', TemplateView.as_view(template_name='index.html'), name='index'),
    path('admin/', admin.site.urls),
    path('schema-viewer/', include('schema_viewer.urls')),
    
    # App URLs
    path('promapp/', include('promapp.urls')),
    path('patientapp/', include('patientapp.urls')),
    
    # Standard logout (login is handled by two-factor)
    path('accounts/logout/', auth_views.LogoutView.as_view(), name='logout'),
    
    prefix_default_language=True,
)

# Serve media files in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

    if getattr(settings, 'DEBUG_TOOLBAR_ENABLED', False):
        urlpatterns += [
            path('__debug__/', include('debug_toolbar.urls')),
        ]
