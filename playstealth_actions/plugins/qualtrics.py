import re
from playwright.async_api import Page
from .base_platform import BasePlatform
from ..telemetry import log_event
from ..trap_detector import analyze_page_traps


class QualtricsPlatform(BasePlatform):
    """Plugin für Qualtrics Survey-Plattform."""

    async def detect(self, page: Page) -> bool:
        return "qualtrics.com" in page.url or await page.locator("#QualtricsSurvey, .Skin").count() > 0

    async def handle_consent(self, page: Page) -> bool:
        try:
            from ..smart_actions import SmartClickAction
            btn = await SmartClickAction().resolve(page, "Accept")
            if btn and await btn.is_visible(timeout=2000):
                await btn.click()
                log_event("plugin", "consent_handled", platform=self.platform_name)
                return True
        except Exception:
            pass
        return False

    async def get_current_step(self, page: Page):
        q_text = await page.locator(".QuestionText").first.inner_text(timeout=4000)
        opts = await page.locator(".ChoiceStructure label, .q-radio, .q-checkbox").all()
        return {"question": q_text.strip(), "option_count": len(opts), "type": "qualtrics_choice"}

    async def answer_question(self, page: Page, answer_data) -> bool:
        option_texts = []
        labels = await page.locator(".ChoiceStructure label, .q-radio, .q-checkbox").all()
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
            opts = await page.locator(".ChoiceStructure label, .q-radio, .q-checkbox").all()
            if 0 <= answer_data < len(opts):
                await opts[answer_data].click()
                log_event(
                    "plugin",
                    "answer_selected",
                    platform=self.platform_name,
                    metadata={"index": answer_data},
                )
                return True
        return False

    async def navigate_next(self, page: Page) -> bool:
        try:
            next_btn = page.locator("#NextButton")
            if await next_btn.is_visible():
                await next_btn.click()
                log_event("plugin", "navigate_next", platform=self.platform_name)
                return True
        except Exception:
            pass
        return False

    async def is_completed(self, page: Page) -> bool:
        end_of_survey = page.locator("#EndOfSurvey")
        if await end_of_survey.count() > 0:
            return True
        content = await page.content()
        return bool(re.search(r"(response.*recorded|thank.*you)", content, re.I))
