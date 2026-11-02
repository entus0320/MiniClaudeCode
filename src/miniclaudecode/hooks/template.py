"""Hook action template rendering."""

from __future__ import annotations

import re

from miniclaudecode.hooks.core import HookContext
from miniclaudecode.hooks.matcher import get_context_value

_TEMPLATE_PATTERN = re.compile(r"\$\{([^}]+)\}")


def render_template(text: str, context: HookContext) -> str:
    """Render ${field.path} placeholders with HookContext values."""

    def replace(match: re.Match[str]) -> str:
        field = match.group(1).strip()
        if not field:
            return ""
        return get_context_value(context, field)

    return _TEMPLATE_PATTERN.sub(replace, text)
