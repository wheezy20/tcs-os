from django.contrib import admin
from django.urls import path, include
from django.views.generic import RedirectView, TemplateView

urlpatterns = [
    # Root has no page of its own — send visitors straight to the inquiry
    # form, the actual top-of-funnel entry point for a prospective parent.
    path("", RedirectView.as_view(url="/inquiry", permanent=False)),

    # Public admissions forms — served directly by Django so everything
    # (forms + API + admin) lives under the one admissions.tcsch.edu.gh
    # subdomain. Templates live in templates/public/, adapted copies of
    # frontend/*.html (see that directory's own copies for local static-file
    # preview outside Django) — see docs/admissions/04-build-log.md for why
    # these are separate files rather than one shared source.
    path("inquiry", TemplateView.as_view(template_name="public/inquiry.html")),
    path("apply", TemplateView.as_view(template_name="public/apply.html")),
    path("offer", TemplateView.as_view(template_name="public/offer.html")),

    path("admin/", admin.site.urls),
    path("api/admissions/", include("admissions.urls")),
    # future modules: path("api/hr/", include("hr.urls")), etc.
]
