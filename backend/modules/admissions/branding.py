"""Callables for django-unfold's SITE_LOGO/SITE_ICON config.

Unfold accepts either a raw string path or a dotted-path-to-a-callable for
these settings (see unfold.sites.BaseAdminSite._get_value). We use callables
rather than hardcoding a path string in settings.py, because settings.py is
imported before the app registry — and more importantly, the project's
staticfiles storage (whitenoise's CompressedManifestStaticFilesStorage)
serves content-hashed filenames in production, which only django's static()
helper resolves correctly (via the manifest built by collectstatic). Calling
static() here, at request time, gets that resolution right in both dev and
production; a hardcoded string in settings.py would not.
"""

from django.templatetags.static import static


def logo_light(request):
    """Header/sidebar logo when the light theme is active — mint-leaf
    laurel on white, per the brand guide's light-background rule."""
    return static("admissions/branding/full-logo-horizontal-white-bg.png")


def logo_dark(request):
    """Header/sidebar logo when the dark theme is active — lime-green
    laurel on dark, per the brand guide's dark-background rule."""
    return static("admissions/branding/full-logo-horizontal-teal-bg.png")


def favicon(request):
    return static("admissions/branding/logomark-white-bg.png")
