from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/admissions/", include("admissions.urls")),
    # future modules: path("api/hr/", include("hr.urls")), etc.
]
