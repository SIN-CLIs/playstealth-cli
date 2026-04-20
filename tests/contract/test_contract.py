import pytest

from opensin_bridge.contract import (
    BRIDGE_CONTRACT_VERSION,
    BridgeError,
    METHODS,
    ERROR_CODES,
    classify_error,
    get_method,
    is_idempotent,
    retry_hint_for,
)


def test_version_shape():
    major, minor, patch = BRIDGE_CONTRACT_VERSION.split(".")
    assert major.isdigit() and minor.isdigit() and patch.isdigit()


def test_methods_unique():
    names = [m.name for m in METHODS]
    assert len(names) == len(set(names))


def test_every_method_namespaced():
    for m in METHODS:
        assert "." in m.name, m.name


def test_get_method_roundtrip():
    assert get_method("dom.click").category == "dom"
    assert is_idempotent("dom.snapshot") is True
    assert retry_hint_for("dom.click") == "retry-after-refresh"


def test_get_method_missing():
    with pytest.raises(BridgeError) as excinfo:
        get_method("does.not.exist")
    assert excinfo.value.code == "METHOD_NOT_FOUND"


def test_classify_from_dict():
    err = classify_error({"code": "TAB_NOT_FOUND", "message": "gone", "retry_hint": "abort"})
    assert isinstance(err, BridgeError)
    assert err.code == "TAB_NOT_FOUND"
    assert err.retry_hint == "abort"


def test_classify_from_exception():
    err = classify_error(RuntimeError("boom"))
    assert err.code == "INTERNAL"


def test_error_codes_frozen():
    assert "CONTRACT_MISMATCH" in ERROR_CODES
    assert "TARGET_NOT_FOUND" in ERROR_CODES
