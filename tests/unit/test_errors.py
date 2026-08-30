import pytest

from quorum.core.errors import ConfigError, QuorumError


def test_hierarchy():
    assert issubclass(ConfigError, QuorumError)
    assert issubclass(QuorumError, Exception)


def test_catchable_as_base():
    with pytest.raises(QuorumError):
        raise ConfigError("missing config key")
