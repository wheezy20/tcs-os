"""Shared whitelist {{name}} substitution — used anywhere staff-authored
text (an email template, a generated-document body) needs merge fields
filled in without executing arbitrary logic.

Extracted from modules/admissions/bulk_email.py (Session 7 of the hr
module's build) rather than duplicated or cross-imported between the two
module apps — this project's per-module-app convention (docs/shared-
stack.md) doesn't cover truly generic, model-agnostic utility code like
this, and neither modules.admissions nor modules.hr should import
directly from the other's internals. Lives at the project-root package
(tcs_os/) rather than either module, since both admissions and hr use it
on equal footing.
"""

import re

PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def render_template(text, context):
    """Simple whitelisted {{name}} substitution — deliberately not Django's
    template engine, which would let staff-authored text execute arbitrary
    {% %} template logic. A mail-merge or a document body doesn't need
    that, and this is safer. An unknown placeholder is left as-is (visibly
    wrong) rather than silently blanked, so a typo shows up in Preview
    instead of vanishing."""
    def replace(match):
        key = match.group(1)
        return str(context[key]) if key in context else match.group(0)
    return PLACEHOLDER_PATTERN.sub(replace, text)
