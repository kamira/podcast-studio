# -*- coding: utf-8 -*-
"""value_checks 的測試(CHG-20261103-01 T1:抽出共用的 is_int)。

L1:`value_checks.is_int` 只對「int 且非 bool」回 True。
L2:讀取方側——`project`、`settings` 改用 `value_checks`,不再有本地 `_is_int`,
且兩者引用的 `value_checks` 與測試直接匯入的是同一個模組物件。
"""
from __future__ import annotations

import pytest

import project
import settings
import value_checks


# ---------- L1:is_int ----------

@pytest.mark.parametrize("v", [0, 1, -3, 2**70])
def test_is_int_接受真正的整數(v):
    assert value_checks.is_int(v) is True


@pytest.mark.parametrize("v", [True, False, 1.0, "1", None, [1]])
def test_is_int_拒絕_bool_與非整數(v):
    assert value_checks.is_int(v) is False


# ---------- L2:讀取方改用 value_checks ----------

def test_project_不再有本地_is_int():
    assert not hasattr(project, "_is_int")


def test_settings_不再有本地_is_int():
    assert not hasattr(settings, "_is_int")


def test_project_與_settings_引用同一個_value_checks模組():
    assert project.value_checks is value_checks
    assert settings.value_checks is value_checks


def test_project_check_percent_仍拒絕_bool():
    with pytest.raises(project.ProjectError):
        project.check_percent(True, "測試")
