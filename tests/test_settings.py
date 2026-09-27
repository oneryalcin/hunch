"""Run settings live in hunch.settings; the old hunch.core names fail loudly instead of doing nothing."""
import pytest

from hunch import core


def test_setting_the_old_core_cap_is_an_error_not_a_silent_no_op():  # else a capped run would run uncapped
    with pytest.raises(AttributeError, match="hunch.settings.MAX_COST"):
        core.MAX_COST = 0.0
