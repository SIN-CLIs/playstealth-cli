"""Dashboard plugin loader with auto-discovery."""
import importlib
import pkgutil
from pathlib import Path
from typing import List, Type, Optional
from .base_dashboard import BaseDashboardPlugin

def load_dashboard_plugins() -> List[Type[BaseDashboardPlugin]]:
    """Load all dashboard plugins from the plugins directory."""
    plugins = []
    plugin_dir = Path(__file__).parent
    
    for _, module_name, _ in pkgutil.iter_modules([str(plugin_dir)]):
        if not module_name.startswith("dashboard_"):
            continue
        try:
            mod = importlib.import_module(f".{module_name}", package=__package__)
            for attr_name in dir(mod):
                attr = getattr(mod, attr_name)
                if isinstance(attr, type) and issubclass(attr, BaseDashboardPlugin) and attr is not BaseDashboardPlugin:
                    plugins.append(attr)
        except Exception as e:
            print(f"⚠️ Dashboard plugin load warning ({module_name}): {e}")
    
    return plugins

def get_dashboard_plugin(platform_name: str) -> Optional[BaseDashboardPlugin]:
    """Get a specific dashboard plugin by platform name."""
    plugins = load_dashboard_plugins()
    for p in plugins:
        if platform_name.lower() in p.__name__.lower():
            return p()
    return None

__all__ = ["load_dashboard_plugins", "get_dashboard_plugin"]
