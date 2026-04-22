import re
from playwright.async_api import Page
from .base_platform import BasePlatform
from ..telemetry import log_event
from ..trap_detector import analyze_page_traps


class HeyPiggyPlatform(BasePlatform):
    """Plugin für HeyPiggy Survey-Plattform."""

    async def detect(self, page: Page) -> bool:
        url = page.url.lower()
        dom_match = await page.locator("[class*='piggy'], [id*='survey-container']").count() > 0
        return "heypiggy" in url or dom_match

    async def handle_consent(self, page: Page) -> bool:
        try:
            from ..smart_actions import SmartClickAction
            btn = await SmartClickAction().resolve(page, "Akzeptieren")
            if btn and await btn.is_visible(timeout=2000):
                await btn.click()
                log_event("plugin", "consent_handled", platform=self.platform_name)
                return True
        except Exception:
            pass
        return False

    async def get_current_step(self, page: Page):
        q_loc = page.locator(".question-title, h2, .q-text, [class*='question']").first
        q_text = await q_loc.inner_text(timeout=4000)
        opts = await page.locator("input[type='radio'], input[type='checkbox'], .option-btn, [role='radio']").all()
        return {"question": q_text.strip(), "option_count": len(opts), "type": "hey_piggy_choice"}

    async def answer_question(self, page: Page, answer_data) -> bool:
        option_texts = []
        labels = await page.locator("label, .option-btn, [role='radio']").all()
        for label in labels:
            try:
                option_texts.append((await label.inner_text()).strip())
            except Exception:
                option_texts.append("")
        trap = await analyze_page_traps(
            page,
            (await self.get_current_step(page)).get("question", ""),
            option_texts,
        )
        if trap["attention_check"] and trap["attention_check"].get("action") == "select_index":
            answer_data = int(trap["attention_check"]["index"])
            log_event(
                "plugin",
                "trap_hit",
                platform=self.platform_name,
                trap_type="attention_check",
                metadata={"instruction": trap["attention_check"].get("instruction", "")[:120]},
            )
        if isinstance(answer_data, int):
            opts = await page.locator("input[type='radio'], input[type='checkbox'], .option-btn, [role='radio']").all()
            if 0 <= answer_data < len(opts):
                await opts[answer_data].click()
                log_event(
                    "plugin",
                    "answer_selected",
                    platform=self.platform_name,
                    metadata={"index": answer_data},
                )
                return True
        elif isinstance(answer_data, str):
            try:
                from ..smart_actions import SmartClickAction
                loc = await SmartClickAction().resolve(page, answer_data)
                if loc:
                    await loc.click()
                    log_event(
                        "plugin",
                        "answer_selected",
                        platform=self.platform_name,
                        metadata={"text": answer_data[:80]},
                    )
                    return True
            except Exception:
                pass
        return False

    async def navigate_next(self, page: Page) -> bool:
        try:
            from ..smart_actions import SmartClickAction
            btn = await SmartClickAction().resolve(page, "Weiter")
            if btn:
                await btn.click()
                log_event("plugin", "navigate_next", platform=self.platform_name)
                return True
        except Exception:
            return False
        return False

    async def is_completed(self, page: Page) -> bool:
        content = await page.content()
        return bool(re.search(r"(vielen dank|danke.*teilnahme|abgeschlossen|survey.*complete)", content, re.I))
