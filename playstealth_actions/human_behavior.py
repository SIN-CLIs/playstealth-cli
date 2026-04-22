"""Human-like behavior engine for PlayStealth.

This module provides human-simulating actions that avoid behavioral detection:
- Bézier curve mouse movements
- Gaussian-distributed delays
- Natural scrolling with acceleration/deceleration
- Idle time simulation (reading pauses)
"""

from __future__ import annotations

import asyncio
import math
import random
from dataclasses import dataclass
from typing import Callable

from playwright.async_api import Page


@dataclass
class Point:
    """A 2D point for mouse coordinates."""

    x: float
    y: float


def bezier_curve(p0: Point, p1: Point, p2: Point, p3: Point, t: float) -> Point:
    """Calculate a point on a cubic Bézier curve at parameter t (0-1)."""
    x = (
        (1 - t) ** 3 * p0.x
        + 3 * (1 - t) ** 2 * t * p1.x
        + 3 * (1 - t) * t**2 * p2.x
        + t**3 * p3.x
    )
    y = (
        (1 - t) ** 3 * p0.y
        + 3 * (1 - t) ** 2 * t * p1.y
        + 3 * (1 - t) * t**2 * p2.y
        + t**3 * p3.y
    )
    return Point(x, y)


def generate_bezier_control_points(start: Point, end: Point) -> tuple[Point, Point]:
    """Generate two random control points for a natural-looking curve.

    The control points are offset perpendicular to the direct path,
    creating a subtle S-curve that mimics human hand movement.
    """
    # Direct vector from start to end
    dx = end.x - start.x
    dy = end.y - start.y
    distance = math.sqrt(dx * dx + dy * dy)

    # Perpendicular offset (human hands don't move in straight lines)
    # Offset is proportional to distance but capped for small movements
    offset_mag = min(distance * 0.2, 50)

    # Random direction for the curve
    direction = random.choice([-1, 1])

    # First control point: 1/3 along the path, offset perpendicular
    cp1_x = start.x + dx / 3 + direction * offset_mag * (dy / distance if distance else 0)
    cp1_y = start.y + dy / 3 - direction * offset_mag * (dx / distance if distance else 0)

    # Second control point: 2/3 along the path, offset opposite direction
    cp2_x = start.x + 2 * dx / 3 - direction * offset_mag * (dy / distance if distance else 0)
    cp2_y = start.y + 2 * dy / 3 + direction * offset_mag * (dx / distance if distance else 0)

    # Add some randomness to make each movement unique
    cp1_x += random.uniform(-10, 10)
    cp1_y += random.uniform(-10, 10)
    cp2_x += random.uniform(-10, 10)
    cp2_y += random.uniform(-10, 10)

    return Point(cp1_x, cp1_y), Point(cp2_x, cp2_y)


def gaussian_delay(mean: float = 0.5, std: float = 0.15) -> float:
    """Generate a Gaussian-distributed delay value.

    Args:
        mean: Mean delay in seconds
        std: Standard deviation for natural variation

    Returns:
        Delay value clamped to positive values
    """
    delay = random.gauss(mean, std)
    return max(0.05, delay)  # Never less than 50ms


async def mouse_move_curve(page: Page, target_x: float, target_y: float, duration: float = None):
    """Move mouse along a Bézier curve to target position.

    Args:
        page: Playwright page instance
        target_x: Target X coordinate
        target_y: Target Y coordinate
        duration: Optional duration in seconds (auto-calculated if None)
    """
    # Get current mouse position (we track it ourselves since Playwright doesn't expose it)
    # For simplicity, we'll start from center of viewport if not tracked
    current_x = getattr(page, "_mouse_x", None)
    current_y = getattr(page, "_mouse_y", None)

    if current_x is None or current_y is None:
        # Default to center of last known viewport
        viewport = page.viewport_size or {"width": 1024, "height": 768}
        current_x = viewport["width"] / 2
        current_y = viewport["height"] / 2

    start = Point(current_x, current_y)
    end = Point(target_x, target_y)

    # Generate control points for the curve
    cp1, cp2 = generate_bezier_control_points(start, end)

    # Calculate duration based on distance (humans move faster for longer distances)
    if duration is None:
        distance = math.sqrt((end.x - start.x) ** 2 + (end.y - start.y) ** 2)
        # Base speed: ~500px per second, with variation
        base_duration = distance / 500
        duration = base_duration * random.uniform(0.8, 1.2)
        duration = max(0.2, min(duration, 2.0))  # Clamp between 200ms and 2s

    # Number of steps affects smoothness
    steps = int(duration * 60)  # ~60 steps per second for smooth movement
    steps = max(steps, 10)  # At least 10 steps

    for i in range(steps + 1):
        t = i / steps
        point = bezier_curve(start, cp1, cp2, end, t)

        # Add micro-jitter (human hands aren't perfectly steady)
        jitter_x = random.uniform(-0.5, 0.5)
        jitter_y = random.uniform(-0.5, 0.5)

        await page.mouse.move(point.x + jitter_x, point.y + jitter_y)

        # Variable delay between steps (not perfectly uniform)
        step_delay = (duration / steps) * random.uniform(0.7, 1.3)
        await asyncio.sleep(step_delay)

    # Update tracked position
    page._mouse_x = target_x  # type: ignore
    page._mouse_y = target_y  # type: ignore


async def human_click(
    page: Page,
    selector: str,
    click_count: int = 1,
    button: str = "left",
    pre_click_delay: float = None,
    post_click_delay: float = None,
) -> bool:
    """Execute a human-like click on an element.

    This replaces page.click() with a more sophisticated approach:
    1. Move mouse along a curve to the element
    2. Random pause before clicking (hesitation)
    3. Mouse down with slight pressure variation
    4. Mouse up after realistic hold time
    5. Post-click reaction delay

    Args:
        page: Playwright page instance
        selector: CSS selector for the target element
        click_count: Number of clicks (1 for single, 2 for double)
        button: Mouse button ('left', 'right', 'middle')
        pre_click_delay: Override for pre-click hesitation (None for auto)
        post_click_delay: Override for post-click delay (None for auto)

    Returns:
        True if click was executed, False if element not found
    """
    try:
        element = await page.query_selector(selector)
        if not element:
            return False

        # Check visibility
        if not await element.is_visible():
            return False

        # Get element bounding box
        box = await element.bounding_box()
        if not box:
            return False

        # Calculate click position with slight offset (humans don't click exact center)
        click_x = box["x"] + box["width"] / 2 + random.uniform(-box["width"] * 0.2, box["width"] * 0.2)
        click_y = box["y"] + box["height"] / 2 + random.uniform(-box["height"] * 0.2, box["height"] * 0.2)

        # Scroll element into view if needed
        await element.scroll_into_view_if_needed(timeout=3000)
        await asyncio.sleep(gaussian_delay(0.2, 0.1))

        # Move mouse to element with curve
        await mouse_move_curve(page, click_x, click_y)

        # Pre-click hesitation (reading/processing time)
        await asyncio.sleep(pre_click_delay or gaussian_delay(0.3, 0.15))

        # Execute click(s) with mouse down/up
        for i in range(click_count):
            if i > 0:
                await asyncio.sleep(gaussian_delay(0.15, 0.05))  # Delay between double-click parts

            # Mouse down
            await page.mouse.down(button=button)
            await asyncio.sleep(random.uniform(0.05, 0.15))  # Button hold time

            # Mouse up
            await page.mouse.up(button=button)

        # Post-click reaction delay (waiting for feedback)
        await asyncio.sleep(post_click_delay or gaussian_delay(0.2, 0.1))

        return True

    except Exception as e:
        print(f"Human click error on {selector}: {e}")
        return False


async def human_type(
    page: Page,
    selector: str,
    text: str,
    delay_mean: float = 80,
    delay_std: float = 30,
) -> bool:
    """Type text with human-like timing variations.

    Humans don't type at constant speed - they pause, make corrections,
    and vary their rhythm based on content difficulty.

    Args:
        page: Playwright page instance
        selector: CSS selector for the input element
        text: Text to type
        delay_mean: Mean delay between keystrokes in ms
        delay_std: Standard deviation for timing variation

    Returns:
        True if typing succeeded, False otherwise
    """
    try:
        element = await page.query_selector(selector)
        if not element:
            return False

        # Click to focus first
        await human_click(page, selector)

        # Clear existing text with Ctrl+A then Delete (more human-like than fill)
        await page.keyboard.press("Control+a")
        await asyncio.sleep(gaussian_delay(0.1, 0.05))
        await page.keyboard.press("Delete")
        await asyncio.sleep(gaussian_delay(0.15, 0.05))

        # Type each character with variable delay
        for char in text:
            await page.keyboard.type(char)
            # Variable delay based on character (special chars take longer)
            char_delay = delay_mean
            if char in "!@#$%^&*()_+-=[]{}|;:,.<>?":
                char_delay *= 1.5  # Special characters take longer
            elif char == " ":
                char_delay *= 0.8  # Spaces are quicker

            await asyncio.sleep(random.gauss(char_delay, delay_std) / 1000)

            # Occasional micro-pause (thinking/hesitation)
            if random.random() < 0.05:  # 5% chance per character
                await asyncio.sleep(gaussian_delay(0.3, 0.1))

        return True

    except Exception as e:
        print(f"Human type error on {selector}: {e}")
        return False


async def human_scroll(
    page: Page,
    delta_y: int,
    duration: float = None,
    horizontal: bool = False,
) -> None:
    """Scroll with natural acceleration and deceleration.

    Human scrolling isn't instant - it has momentum and easing.

    Args:
        page: Playwright page instance
        delta_y: Amount to scroll (positive = down, negative = up)
        duration: Scroll duration in seconds (auto-calculated if None)
        horizontal: If True, scroll horizontally instead
    """
    # Calculate duration based on scroll distance
    if duration is None:
        duration = abs(delta_y) / 1000 * random.uniform(0.3, 0.6)
        duration = max(0.3, min(duration, 2.0))

    steps = 20
    total_scrolled = 0

    for i in range(steps):
        # Ease-in-out function for natural acceleration/deceleration
        t = i / steps
        ease = t * t * (3 - 2 * t)  # Smoothstep function

        # Calculate scroll amount for this step
        target_position = delta_y * ease
        step_delta = target_position - total_scrolled

        if horizontal:
            await page.evaluate(f"window.scrollBy({step_delta}, 0)")
        else:
            await page.evaluate(f"window.scrollBy(0, {step_delta})")

        total_scrolled = target_position

        # Variable delay between scroll events
        await asyncio.sleep(duration / steps * random.uniform(0.7, 1.3))


async def idle_time(page: Page, mean_duration: float = 2.0, std: float = 0.8) -> None:
    """Simulate idle time where a human would be reading or processing.

    During idle time, we can add subtle movements to appear more human:
    - Tiny mouse jitters
    - Occasional blinks (if we had eye tracking)
    - Minimal scroll adjustments

    Args:
        page: Playwright page instance
        mean_duration: Mean idle duration in seconds
        std: Standard deviation for duration
    """
    duration = max(0.5, random.gauss(mean_duration, std))
    elapsed = 0
    interval = 0.3

    while elapsed < duration:
        await asyncio.sleep(interval)
        elapsed += interval

        # Add subtle mouse jitter occasionally
        if random.random() < 0.3:  # 30% chance during idle
            jitter_x = random.uniform(-2, 2)
            jitter_y = random.uniform(-2, 2)
            current_x = getattr(page, "_mouse_x", 512)
            current_y = getattr(page, "_mouse_y", 384)
            await page.mouse.move(current_x + jitter_x, current_y + jitter_y)

        # Occasional tiny scroll (re-adjusting view while reading)
        if random.random() < 0.1:  # 10% chance
            tiny_scroll = random.randint(-10, 10)
            if tiny_scroll != 0:
                await page.evaluate(f"window.scrollBy(0, {tiny_scroll})")


async def human_interact_with_element(
    page: Page,
    selector: str,
    action: str = "click",
    value: str = None,
) -> bool:
    """High-level human interaction with an element.

    This combines multiple human-like behaviors:
    1. Idle time before interaction (reading)
    2. Mouse movement to element
    3. Action execution (click, type, etc.)
    4. Post-action pause

    Args:
        page: Playwright page instance
        selector: CSS selector for the target
        action: Action type ('click', 'type', 'hover', 'focus')
        value: Value for type action

    Returns:
        True if action succeeded
    """
    # Initial idle (reading time before acting)
    await idle_time(page, mean_duration=1.0, std=0.5)

    if action == "click":
        return await human_click(page, selector)
    elif action == "type":
        if value is None:
            raise ValueError("Value required for type action")
        return await human_type(page, selector, value)
    elif action == "hover":
        element = await page.query_selector(selector)
        if element:
            box = await element.bounding_box()
            if box:
                await mouse_move_curve(
                    page,
                    box["x"] + box["width"] / 2,
                    box["y"] + box["height"] / 2,
                )
                return True
        return False
    elif action == "focus":
        element = await page.query_selector(selector)
        if element:
            await element.focus()
            return True
        return False
    else:
        raise ValueError(f"Unknown action: {action}")
