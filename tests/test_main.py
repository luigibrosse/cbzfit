# SPDX-License-Identifier: GPL-3.0-or-later

import importlib
import runpy
import sys
from unittest.mock import Mock

import pytest

import cbzfit.cli as cli_module


def test_module_import_does_not_run_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    main = Mock()
    monkeypatch.setattr(cli_module, "main", main)
    monkeypatch.delitem(
        sys.modules,
        "cbzfit.__main__",
        raising=False,
    )

    imported_module = importlib.import_module("cbzfit.__main__")

    assert imported_module.__name__ == "cbzfit.__main__"
    main.assert_not_called()


def test_module_entry_point_exits_with_main_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    main = Mock(return_value=7)
    monkeypatch.setattr(cli_module, "main", main)
    monkeypatch.delitem(
        sys.modules,
        "cbzfit.__main__",
        raising=False,
    )

    with pytest.raises(SystemExit) as exception_info:
        runpy.run_module(
            "cbzfit.__main__",
            run_name="__main__",
        )

    assert exception_info.value.code == 7
    main.assert_called_once_with()
