"""PluginManager -- moved to core.plugins; every name is kept here.

Plugins and tests that do ``from shared.plugins import PluginManager`` (or
``has_requirement``) keep working.
"""
from core.plugins import (   # noqa: F401
    PluginManager, PLUGINS_DIR, _DEBUG, _dbg, _REQUIREMENT_MODULES,
    requirement_module, has_requirement,
)
