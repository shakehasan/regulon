import re

import pytest

from regulon.core.ids import new_event_id, new_id, new_run_id

ID_RE = re.compile(r"^[a-z][a-z0-9]{0,15}_[0-9a-f]{20}$")


def test_new_id_shape():
    assert ID_RE.match(new_id("run"))


def test_new_id_uniqueness():
    ids = {new_id("x") for _ in range(1000)}
    assert len(ids) == 1000


def test_new_id_time_sortable():
    import time

    a = new_id("t")
    time.sleep(0.002)
    b = new_id("t")
    assert a < b


@pytest.mark.parametrize("bad", ["", "Run", "1run", "run-id", "run_id", "a" * 17])
def test_new_id_rejects_bad_prefix(bad):
    with pytest.raises(ValueError, match="Invalid id prefix"):
        new_id(bad)


def test_convenience_prefixes():
    assert new_run_id().startswith("run_")
    assert new_event_id().startswith("evt_")
