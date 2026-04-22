import pytest

from playstealth_actions.trap_detector import analyze_page_traps, parse_attention_check


def test_parse_attention_check_text_match() -> None:
    result = parse_attention_check(
        "Please select the second option to continue.",
        ["First option", "Second option", "Third option"],
    )
    assert result is not None
    assert result["action"] == "select_index"
    assert result["index"] == 1


@pytest.mark.asyncio
async def test_analyze_page_traps_detects_attention_check(page) -> None:
    await page.set_content(
        """
        <html><body>
          <div id='survey-modal'>
            <p>Please select the second option.</p>
            <label name='answerOption'><input type='radio' /> First option</label>
            <label name='answerOption'><input type='radio' /> Second option</label>
          </div>
        </body></html>
        """
    )
    result = await analyze_page_traps(
        page, "Please select the second option.", ["First option", "Second option"]
    )
    assert result["is_safe"] is False
    assert result["attention_check"]["index"] == 1
