import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import session_store


@pytest.mark.asyncio
async def test_restore_session_injects_cookie_url(tmp_path: Path):
    cache = tmp_path / "session_cache.json"
    cache.write_text(
        json.dumps(
            {
                "saved_at": "2026-04-19T23:00:00Z",
                "domains": {
                    "heypiggy.com": {
                        "cookies": [
                            {
                                "name": "session",
                                "value": "abc123",
                                "domain": ".heypiggy.com",
                                "path": "/",
                                "secure": True,
                                "hostOnly": True,
                            }
                        ]
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    execute_bridge = AsyncMock(return_value={"ok": True})

    result = await session_store.restore_session(
        execute_bridge,
        {"tabId": 7},
        "https://www.heypiggy.com/login",
        cache_path=cache,
    )

    assert result["restored"] is True
    assert result["cookies_set"] == 1
    execute_bridge.assert_awaited()

    method, params = execute_bridge.await_args_list[0].args
    assert method == "set_cookie"
    assert params["url"] == "https://www.heypiggy.com/"
    assert "hostOnly" not in params
