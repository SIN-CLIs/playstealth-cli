"""
Tool Registry - Central registry for all survey automation actions.

This module provides a unified interface for executing survey actions,
including smart selectors, human-like interactions, and stealth checks.
"""

from typing import Any, Callable, Dict, List, Optional


class ToolRegistry:
    """Central registry for survey automation tools."""

    def __init__(self):
        self._tools: Dict[str, Dict[str, Any]] = {}
        self._register_default_tools()

    def _register_default_tools(self):
        """Register all default tools."""

        # Smart Click Tool
        self.register(
            name="smart-click",
            description="Click element using multi-strategy selector resolution",
            category="interaction",
            handler=self._smart_click_handler,
            parameters={
                "target": {
                    "type": "string",
                    "required": True,
                    "description": "Text or selector to find",
                }
            },
        )

        # Smart Type Tool
        self.register(
            name="smart-type",
            description="Type text into input field using smart selector resolution",
            category="interaction",
            handler=self._smart_type_handler,
            parameters={
                "target": {
                    "type": "string",
                    "required": True,
                    "description": "Label, placeholder, or selector",
                },
                "text": {"type": "string", "required": True, "description": "Text to type"},
            },
        )

        # Smart Select Tool
        self.register(
            name="smart-select",
            description="Select option from dropdown using smart resolution",
            category="interaction",
            handler=self._smart_select_handler,
            parameters={
                "target": {
                    "type": "string",
                    "required": True,
                    "description": "Dropdown label or selector",
                },
                "option": {
                    "type": "string",
                    "required": True,
                    "description": "Option text or value to select",
                },
            },
        )

        # Human Click Tool
        self.register(
            name="human-click",
            description="Click with human-like mouse movement and delays",
            category="interaction",
            handler=self._human_click_handler,
            parameters={
                "selector": {
                    "type": "string",
                    "required": True,
                    "description": "CSS selector or XPath",
                }
            },
        )

        # Human Type Tool
        self.register(
            name="human-type",
            description="Type with human-like variable speed and pauses",
            category="interaction",
            handler=self._human_type_handler,
            parameters={
                "selector": {
                    "type": "string",
                    "required": True,
                    "description": "Input field selector",
                },
                "text": {"type": "string", "required": True, "description": "Text to type"},
            },
        )

        # Human Scroll Tool
        self.register(
            name="human-scroll",
            description="Scroll with human-like acceleration/deceleration",
            category="interaction",
            handler=self._human_scroll_handler,
            parameters={
                "target_y": {
                    "type": "integer",
                    "required": True,
                    "description": "Target scroll position in pixels",
                }
            },
        )

        # Idle Time Tool
        self.register(
            name="idle-time",
            description="Simulate reading/thinking time with micro-movements",
            category="interaction",
            handler=self._idle_time_handler,
            parameters={
                "duration": {
                    "type": "float",
                    "required": False,
                    "description": "Base duration in seconds (default: 2.0)",
                }
            },
        )

        # Check Stealth Tool
        self.register(
            name="check-stealth",
            description="Run comprehensive anti-detection leak check",
            category="diagnostics",
            handler=self._check_stealth_handler,
            parameters={},
        )

        # Check WebGL Tool
        self.register(
            name="check-webgl",
            description="Check for WebGL fingerprinting leaks",
            category="diagnostics",
            handler=self._check_webgl_handler,
            parameters={},
        )

        # Check Headless Tool
        self.register(
            name="check-headless",
            description="Check for headless browser indicators",
            category="diagnostics",
            handler=self._check_headless_handler,
            parameters={},
        )

        self.register(
            name="detect-traps",
            description="Detect honeypots and attention checks on the current page",
            category="diagnostics",
            handler=self._detect_traps_handler,
            parameters={},
        )

        self.register(
            name="telemetry-summary",
            description="Show aggregated anonymous telemetry metrics",
            category="diagnostics",
            handler=self._telemetry_summary_handler,
            parameters={},
        )

        self.register(
            name="ban-risk",
            description="Estimate ban risk from telemetry signals",
            category="diagnostics",
            handler=self._ban_risk_handler,
            parameters={},
        )

        # Wait for Element Tool
        self.register(
            name="wait-for-element",
            description="Wait for element to be visible with timeout",
            category="utility",
            handler=self._wait_for_element_handler,
            parameters={
                "selector": {"type": "string", "required": True, "description": "CSS selector"},
                "timeout": {
                    "type": "integer",
                    "required": False,
                    "description": "Timeout in ms (default: 5000)",
                },
            },
        )

        # Screenshot Tool
        self.register(
            name="screenshot",
            description="Take a screenshot of the current page",
            category="utility",
            handler=self._screenshot_handler,
            parameters={
                "path": {"type": "string", "required": False, "description": "File path (optional)"}
            },
        )

    def register(
        self,
        name: str,
        description: str,
        category: str,
        handler: Callable,
        parameters: Dict[str, Dict[str, Any]],
    ):
        """Register a new tool."""
        self._tools[name] = {
            "name": name,
            "description": description,
            "category": category,
            "handler": handler,
            "parameters": parameters,
        }

    def get(self, name: str) -> Optional[Dict[str, Any]]:
        """Get tool by name."""
        return self._tools.get(name)

    def list_tools(self, category: Optional[str] = None) -> List[str]:
        """List all registered tools, optionally filtered by category."""
        if category:
            return [t["name"] for t in self._tools.values() if t["category"] == category]
        return list(self._tools.keys())

    async def execute(self, name: str, page: Any, **kwargs) -> Any:
        """Execute a tool by name."""
        tool = self.get(name)
        if not tool:
            raise ValueError(f"Unknown tool: {name}")

        # Validate required parameters
        for param_name, param_def in tool["parameters"].items():
            if param_def.get("required") and param_name not in kwargs:
                raise ValueError(f"Missing required parameter: {param_name}")

        return await tool["handler"](page, **kwargs)

    # === Handler Implementations ===

    async def _smart_click_handler(self, page, target: str, **kwargs):
        """Handle smart-click tool execution."""
        from .smart_actions import SmartClickAction

        action = SmartClickAction()
        result = await action.execute(page, target)
        return {"success": True, "action": "smart-click", "target": target, "result": result}

    async def _smart_type_handler(self, page, target: str, text: str, **kwargs):
        """Handle smart-type tool execution."""
        from .smart_actions import SmartTypeAction

        action = SmartTypeAction()
        result = await action.execute(page, target, text)
        return {
            "success": True,
            "action": "smart-type",
            "target": target,
            "text": text,
            "result": result,
        }

    async def _smart_select_handler(self, page, target: str, option: str, **kwargs):
        """Handle smart-select tool execution."""
        from .smart_actions import SmartSelectAction

        action = SmartSelectAction()
        result = await action.execute(page, target, option)
        return {
            "success": True,
            "action": "smart-select",
            "target": target,
            "option": option,
            "result": result,
        }

    async def _human_click_handler(self, page, selector: str, **kwargs):
        """Handle human-click tool execution."""
        from .human_behavior import human_click

        await human_click(page, selector)
        return {"success": True, "action": "human-click", "selector": selector}

    async def _human_type_handler(self, page, selector: str, text: str, **kwargs):
        """Handle human-type tool execution."""
        from .human_behavior import human_type

        await human_type(page, selector, text)
        return {"success": True, "action": "human-type", "selector": selector, "text": text}

    async def _human_scroll_handler(self, page, target_y: int, **kwargs):
        """Handle human-scroll tool execution."""
        from .human_behavior import human_scroll

        await human_scroll(page, target_y)
        return {"success": True, "action": "human-scroll", "target_y": target_y}

    async def _idle_time_handler(self, page, duration: float = 2.0, **kwargs):
        """Handle idle-time tool execution."""
        from .human_behavior import idle_time

        await idle_time(page, duration)
        return {"success": True, "action": "idle-time", "duration": duration}

    async def _check_stealth_handler(self, page, **kwargs):
        """Handle check-stealth tool execution."""
        from .diagnose_benchmark import full_stealth_check

        result = await full_stealth_check(page)
        return {"success": True, "action": "check-stealth", "result": result}

    async def _check_webgl_handler(self, page, **kwargs):
        """Handle check-webgl tool execution."""
        from .diagnose_benchmark import check_webgl_leaks

        result = await check_webgl_leaks(page)
        return {"success": True, "action": "check-webgl", "result": result}

    async def _check_headless_handler(self, page, **kwargs):
        """Handle check-headless tool execution."""
        from .diagnose_benchmark import check_headless_indicators

        result = await check_headless_indicators(page)
        return {"success": True, "action": "check-headless", "result": result}

    async def _detect_traps_handler(self, page, **kwargs):
        """Handle trap detection tool execution."""
        from .diagnostic_common import detect_traps

        result = await detect_traps(page)
        return {"success": True, "action": "detect-traps", "result": result}

    async def _telemetry_summary_handler(self, page, **kwargs):
        """Handle telemetry summary tool execution."""
        from .diagnostic_common import telemetry_summary

        result = telemetry_summary()
        return {"success": True, "action": "telemetry-summary", "result": result}

    async def _ban_risk_handler(self, page, **kwargs):
        """Handle ban risk tool execution."""
        from .diagnostic_common import ban_risk_summary

        result = ban_risk_summary()
        return {"success": True, "action": "ban-risk", "result": result}

    async def _wait_for_element_handler(self, page, selector: str, timeout: int = 5000, **kwargs):
        """Handle wait-for-element tool execution."""
        await page.wait_for_selector(selector, state="visible", timeout=timeout)
        return {
            "success": True,
            "action": "wait-for-element",
            "selector": selector,
            "timeout": timeout,
        }

    async def _screenshot_handler(self, page, path: Optional[str] = None, **kwargs):
        """Handle screenshot tool execution."""
        import tempfile
        import os

        if not path:
            fd, path = tempfile.mkstemp(suffix=".png")
            os.close(fd)

        await page.screenshot(path=path)
        return {"success": True, "action": "screenshot", "path": path}


# Global registry instance
registry = ToolRegistry()


def get_registry() -> ToolRegistry:
    """Get the global tool registry."""
    return registry


def list_all_tools() -> Dict[str, List[str]]:
    """List all tools grouped by category."""
    reg = get_registry()
    categories = {}

    for tool_name in reg.list_tools():
        tool = reg.get(tool_name)
        cat = tool["category"]
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(tool_name)

    return categories
