from playstealth_actions.state_store import save_state, load_state
from playstealth_actions.survey_state import create_state


def test_state_store_roundtrip(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PLAYSTEALTH_STATE_PATH", str(tmp_path / "state.json"))
    state = create_state(3)
    state.current_url = "https://example.com"
    save_state(state)
    loaded = load_state()
    assert loaded is not None
    assert loaded["survey_index"] == 3
    assert loaded["current_url"] == "https://example.com"
