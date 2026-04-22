from playstealth_actions.tool_manifest import build_manifest


def test_manifest_contains_core_tools() -> None:
    manifest = build_manifest()
    names = {tool["name"] for tool in manifest["tools"]}
    assert "open-list" in names
    assert "run-survey" in names
    assert "resume-survey" in names
