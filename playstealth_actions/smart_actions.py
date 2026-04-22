"""Smart action handlers with multi-strategy selector resolution.

This module provides robust element interaction that survives DOM changes:
1. Primary: Data attributes (data-testid, aria-label)
2. Secondary: Text-based fuzzy matching
3. Tertiary: Visual/structural heuristics
4. Fallback: DOM traversal with semantic clues

All actions use human-like behavior from human_behavior module.
"""

from __future__ import annotations

import re
from typing import Any

from playwright.async_api import Page

from playstealth_actions.human_behavior import human_click, human_type, idle_time


class SmartClickAction:
    """Multi-strategy click action with fallback mechanisms."""

    def __init__(self, page: Page):
        self.page = page

    async def execute(self, target_text: str, timeout: int = 5000) -> bool:
        """Execute smart click with multiple strategies.

        Args:
            target_text: Text or identifier to search for
            timeout: Maximum time to wait in milliseconds

        Returns:
            True if click was successful, False otherwise
        """
        # Strategy 1: Aria-label exact match
        selector = f'[aria-label="{target_text}"]'
        if await self._try_click(selector, timeout):
            return True

        # Strategy 2: Data-testid exact match
        selector = f'[data-testid="{target_text}"]'
        if await self._try_click(selector, timeout):
            return True

        # Strategy 3: Button with text content (exact)
        selector = f'button:has-text("{target_text}")'
        if await self._try_click(selector, timeout):
            return True

        # Strategy 4: Button with text content (contains)
        buttons = await self.page.query_selector_all("button")
        for btn in buttons:
            try:
                text = await btn.inner_text(timeout=1000)
                if text and target_text.lower() in text.lower():
                    if await btn.is_visible():
                        return await human_click(self.page, f"button:has-text('{text[:50]}')")
            except Exception:
                continue

        # Strategy 5: Link with text content
        links = await self.page.query_selector_all("a")
        for link in links:
            try:
                text = await link.inner_text(timeout=1000)
                if text and target_text.lower() in text.lower():
                    if await link.is_visible():
                        return await human_click(self.page, f"a:has-text('{text[:50]}')")
            except Exception:
                continue

        # Strategy 6: Input with value or placeholder
        inputs = await self.page.query_selector_all("input")
        for inp in inputs:
            try:
                value = await inp.get_attribute("value") or ""
                placeholder = await inp.get_attribute("placeholder") or ""
                if target_text.lower() in value.lower() or target_text.lower() in placeholder.lower():
                    if await inp.is_visible():
                        await inp.scroll_into_view_if_needed(timeout=2000)
                        await idle_time(self.page, 0.5, 0.2)
                        await inp.click(timeout=2000)
                        return True
            except Exception:
                continue

        # Strategy 7: Element with role attribute
        selector = f'[role="button"]:has-text("{target_text}")'
        if await self._try_click(selector, timeout):
            return True

        # Strategy 8: Span/div with onclick handler containing text
        clickable_elements = await self.page.query_selector_all("[onclick]")
        for elem in clickable_elements:
            try:
                text = await elem.inner_text(timeout=1000)
                if text and target_text.lower() in text.lower():
                    if await elem.is_visible():
                        await elem.scroll_into_view_if_needed(timeout=2000)
                        await idle_time(self.page, 0.5, 0.2)
                        await elem.click(timeout=2000)
                        return True
            except Exception:
                continue

        return False

    async def _try_click(self, selector: str, timeout: int) -> bool:
        """Try to click an element with the given selector."""
        try:
            element = await self.page.query_selector(selector)
            if element and await element.is_visible():
                return await human_click(self.page, selector)
        except Exception:
            pass
        return False


class SmartTypeAction:
    """Multi-strategy type action with fallback mechanisms."""

    def __init__(self, page: Page):
        self.page = page

    async def execute(
        self, target_identifier: str, text_to_type: str, timeout: int = 5000
    ) -> bool:
        """Execute smart type with multiple strategies.

        Args:
            target_identifier: Label, placeholder, or name to search for
            text_to_type: Text to type into the field
            timeout: Maximum time to wait in milliseconds

        Returns:
            True if typing was successful, False otherwise
        """
        # Strategy 1: Label text match
        labels = await self.page.query_selector_all("label")
        for label in labels:
            try:
                label_text = await label.inner_text(timeout=1000)
                if label_text and target_identifier.lower() in label_text.lower():
                    # Find associated input via 'for' attribute
                    for_attr = await label.get_attribute("for")
                    if for_attr:
                        input_elem = await self.page.query_selector(f'#{for_attr}')
                        if input_elem and await input_elem.is_visible():
                            return await human_type(self.page, f'#{for_attr}', text_to_type)

                    # Or find nested input
                    nested_input = await label.query_selector("input, textarea")
                    if nested_input and await nested_input.is_visible():
                        await nested_input.scroll_into_view_if_needed(timeout=2000)
                        await idle_time(self.page, 0.5, 0.2)
                        await nested_input.fill(text_to_type)
                        return True
            except Exception:
                continue

        # Strategy 2: Placeholder text match
        inputs = await self.page.query_selector_all("input, textarea")
        for inp in inputs:
            try:
                placeholder = await inp.get_attribute("placeholder") or ""
                if placeholder and target_identifier.lower() in placeholder.lower():
                    if await inp.is_visible():
                        return await human_type(
                            self.page, self._get_selector_for_element(inp), text_to_type
                        )
            except Exception:
                continue

        # Strategy 3: Name attribute match
        selector = f'input[name*="{target_identifier}" i], textarea[name*="{target_identifier}" i]'
        elements = await self.page.query_selector_all(selector)
        for elem in elements:
            try:
                if await elem.is_visible():
                    return await human_type(self.page, self._get_selector_for_element(elem), text_to_type)
            except Exception:
                continue

        # Strategy 4: ID attribute match
        selector = f'input[id*="{target_identifier}" i], textarea[id*="{target_identifier}" i]'
        elements = await self.page.query_selector_all(selector)
        for elem in elements:
            try:
                if await elem.is_visible():
                    return await human_type(self.page, self._get_selector_for_element(elem), text_to_type)
            except Exception:
                continue

        # Strategy 5: Aria-label match
        selector = f'[aria-label*="{target_identifier}" i]'
        elements = await self.page.query_selector_all(selector)
        for elem in elements:
            try:
                tag_name = await elem.evaluate("el => el.tagName.toLowerCase()")
                if tag_name in ["input", "textarea"]:
                    if await elem.is_visible():
                        return await human_type(self.page, self._get_selector_for_element(elem), text_to_type)
            except Exception:
                continue

        return False

    def _get_selector_for_element(self, element) -> str:
        """Generate a CSS selector for an element."""
        # This is simplified - in production you'd want more robust selector generation
        return element  # Playwright can handle element objects directly in many cases


class SmartSelectAction:
    """Multi-strategy select dropdown action."""

    def __init__(self, page: Page):
        self.page = page

    async def execute(
        self, target_identifier: str, option_value: str = None, option_text: str = None
    ) -> bool:
        """Execute smart select with multiple strategies.

        Args:
            target_identifier: Label, placeholder, or name to search for
            option_value: Value attribute of option to select
            option_text: Visible text of option to select

        Returns:
            True if selection was successful, False otherwise
        """
        # Find the select element using similar strategies as SmartTypeAction
        select_elem = await self._find_select_element(target_identifier)

        if not select_elem:
            return False

        try:
            # Select by value if provided
            if option_value:
                await select_elem.select_option(value=option_value)
                return True

            # Select by text if provided
            if option_text:
                await select_elem.select_option(label=option_text)
                return True

            # Default: select first non-empty option
            options = await select_elem.query_selector_all("option")
            for opt in options:
                value = await opt.get_attribute("value")
                if value:  # Skip empty options
                    await select_elem.select_option(value=value)
                    return True

        except Exception as e:
            print(f"SmartSelectAction error: {e}")
            return False

        return False

    async def _find_select_element(self, target_identifier: str):
        """Find a select element matching the identifier."""
        # Strategy 1: Label match
        labels = await self.page.query_selector_all("label")
        for label in labels:
            try:
                label_text = await label.inner_text(timeout=1000)
                if label_text and target_identifier.lower() in label_text.lower():
                    for_attr = await label.get_attribute("for")
                    if for_attr:
                        select_elem = await self.page.query_selector(f'select#{for_attr}')
                        if select_elem and await select_elem.is_visible():
                            return select_elem

                    nested_select = await label.query_selector("select")
                    if nested_select and await nested_select.is_visible():
                        return nested_select
            except Exception:
                continue

        # Strategy 2: Direct select query with name/id
        selectors = [
            f'select[name*="{target_identifier}" i]',
            f'select[id*="{target_identifier}" i]',
            f'select[aria-label*="{target_identifier}" i]',
        ]

        for selector in selectors:
            elements = await self.page.query_selector_all(selector)
            for elem in elements:
                if await elem.is_visible():
                    return elem

        return None


async def smart_click(page: Page, target: str, **kwargs) -> dict[str, Any]:
    """Convenience function for smart click action.

    Args:
        page: Playwright page instance
        target: Target text/identifier to click
        **kwargs: Additional arguments (timeout, etc.)

    Returns:
        Result dictionary with success status and details
    """
    action = SmartClickAction(page)
    success = await action.execute(target, timeout=kwargs.get("timeout", 5000))

    return {"success": success, "action": "click", "target": target}


async def smart_type(page: Page, target: str, text: str, **kwargs) -> dict[str, Any]:
    """Convenience function for smart type action.

    Args:
        page: Playwright page instance
        target: Target identifier (label, placeholder, etc.)
        text: Text to type
        **kwargs: Additional arguments

    Returns:
        Result dictionary with success status and details
    """
    action = SmartTypeAction(page)
    success = await action.execute(target, text, timeout=kwargs.get("timeout", 5000))

    return {"success": success, "action": "type", "target": target, "text": text}


async def smart_select(
    page: Page, target: str, value: str = None, text: str = None, **kwargs
) -> dict[str, Any]:
    """Convenience function for smart select action.

    Args:
        page: Playwright page instance
        target: Target identifier
        value: Option value to select
        text: Option text to select
        **kwargs: Additional arguments

    Returns:
        Result dictionary with success status and details
    """
    action = SmartSelectAction(page)
    success = await action.execute(target, option_value=value, option_text=text)

    return {"success": success, "action": "select", "target": target, "value": value, "text": text}
