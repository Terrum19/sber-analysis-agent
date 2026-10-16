"""Unit-тесты WASM песочницы (stub)."""

import pytest

from app.core.sandbox.wasm_runtime import WASMSandbox


def test_wasm_stub_returns_payload():
    sandbox = WASMSandbox(timeout_s=5, mem_mb=256)
    result = sandbox.run("print('hello')", {"data": [1, 2, 3]})
    assert result["status"] == "stub"
    assert result["result"] == [1, 2, 3]


def test_wasm_stub_no_payload():
    sandbox = WASMSandbox()
    result = sandbox.run("print('hello')")
    assert result["status"] == "stub"
    assert result["result"] == []
