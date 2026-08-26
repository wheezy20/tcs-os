from django.conf import settings
from django.contrib import admin
from django.urls import path, include
from django.views.generic import RedirectView, TemplateView

# Passed to every public form template as {{ TURNSTILE_SITE_KEY }}. The site
# key is public by design (like a Stripe publishable key, not a secret), so
# rendering it from settings rather than baking a literal into the template
# means the real Cloudflare site key never has to be hand-copied into
# templates/public/*.html — set TURNSTILE_SITE_KEY in the environment and
# these three pages pick it up automatically. Defaults to Cloudflare's
# published "always passes" test key, which is why local dev needs no
# Cloudflare account of its own — see admissions/turnstile.py.
_TURNSTILE_CONTEXT = {"TURNSTILE_SITE_KEY": settings.TURNSTILE_SITE_KEY}

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
    path("inquiry", TemplateView.as_view(template_name="public/inquiry.html", extra_context=_TURNSTILE_CONTEXT)),
    path("apply", TemplateView.as_view(template_name="public/apply.html", extra_context=_TURNSTILE_CONTEXT)),
    path("offer", TemplateView.as_view(template_name="public/offer.html", extra_context=_TURNSTILE_CONTEXT)),

    path("admin/", admin.site.urls),
    path("api/admissions/", include("admissions.urls")),
    # future modules: path("api/hr/", include("hr.urls")), etc.
]
