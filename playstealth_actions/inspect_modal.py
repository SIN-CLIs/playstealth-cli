"""Inspect the survey modal for debugging and routing."""

from __future__ import annotations


async def run(page) -> None:
    """Print the visible survey modal details."""
    modal = page.locator("#survey-modal")
    visible = await modal.is_visible()
    print(f"🪟 survey-modal visible: {visible}")
    if not visible:
        raise RuntimeError("survey-modal not visible")

    print(f"🧾 survey-modal text: {(await modal.inner_text(timeout=3000))[:600]!r}")
    controls = modal.locator("button, input, label, [role='button']")
    count = await controls.count()
    print(f"🎛️ survey-modal controls: {count}")
    for i in range(min(count, 24)):
        ctl = controls.nth(i)
        try:
            outer = await ctl.evaluate(
                "el => ({tag: el.tagName, id: el.id || '', cls: el.className || '', name: el.getAttribute('name') || '', onclick: el.getAttribute('onclick') || '', type: el.getAttribute('type') || '', text: (el.innerText || '').trim().slice(0, 120), html: el.outerHTML.slice(0, 300)})"
            )
        except Exception:
            outer = {
                "tag": "?",
                "id": "",
                "cls": "",
                "name": "",
                "onclick": "",
                "type": "",
                "text": "",
                "html": "",
            }
        print(f"   • control[{i}]: {outer}")
