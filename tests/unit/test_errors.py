import pytest

from regulon.core.errors import ConfigError, RegulonError


def test_hierarchy():
    assert issubclass(ConfigError, RegulonError)
    assert issubclass(RegulonError, Exception)


def test_catchable_as_base():
    with pytest.raises(RegulonError):
        raise ConfigError("missing config key")
