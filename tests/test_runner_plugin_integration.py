import pytest

from playstealth_actions.simple_survey_runner import execute_survey_flow


class FakeContext:
    async def storage_state(self, path: str) -> None:
        from pathlib import Path

        Path(path).write_text("{}", encoding="utf-8")


@pytest.mark.asyncio
async def test_execute_survey_flow_uses_plugin_path(page, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("PLAYSTEALTH_STATE_DIR", str(tmp_path))

    await page.set_content(
        """
        <html><body>
          <div class='question-title'>Bitte wählen Sie die zweite Option.</div>
          <label name='answerOption'><input type='radio' name='q1' value='1' /> Erste Option</label>
          <label name='answerOption'><input type='radio' name='q1' value='2' /> Zweite Option</label>
          <button id='next-step' onclick="window.__clicked = true">Weiter</button>
        </body></html>
        """
    )

    class FakePlatform:
        platform_name = "FakePlatform"

        async def get_current_step(self, page):
            return {"question": "Bitte wählen Sie die zweite Option.", "option_count": 2, "type": "fake_choice"}

        async def answer_question(self, page, answer_data):
            opts = await page.locator("input[type='radio']").all()
            await opts[answer_data].check(force=True)
            return True

        async def navigate_next(self, page):
            await page.locator("#next-step").click()
            return True

        async def is_completed(self, page):
            return await page.evaluate("window.__clicked === true") is True

    monkeypatch.setattr(
        "playstealth_actions.simple_survey_runner.load_plugins",
        lambda: [FakePlatform],
    )

    async def _detect_platform(page, plugins):
        return FakePlatform()

    monkeypatch.setattr(
        "playstealth_actions.simple_survey_runner.detect_platform",
        _detect_platform,
    )

    result = await execute_survey_flow(
        page=page,
        context=FakeContext(),
        start_url="about:blank",
        max_steps=1,
        session_id="test_session",
        strategy_name="persona",
        strategy_persona="neutral",
    )

    assert result["success"] is True
    assert result["steps_completed"] == 1
    assert await page.eval_on_selector("input[value='2']", "el => el.checked") is True
