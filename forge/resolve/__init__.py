"""The resolver: from a structured plan to exact, checked, built parts.

`forge.plan` reads the words of a plan; this package works out the geometry.
See README.md in this folder.
"""

from forge.resolve.resolver import Reply, Resolution, resolve_plan, resolve_text

__all__ = ["Reply", "Resolution", "resolve_plan", "resolve_text"]
