import pytest

from backend.utils.speaker_names.placeholder import is_placeholder_speaker_name


@pytest.mark.parametrize(
    "name",
    [
        None,
        "",
        "   ",
        "SPEAKER_03",
        "Speaker 1",
        "unknown",
        "New Voice 3",
        "N:L0",
        "n:l7",
        " N:L12 ",
        "C:SPEAKER_02",
    ],
)
def test_a_fallback_label_is_a_placeholder(name):
    assert is_placeholder_speaker_name(name)


@pytest.mark.parametrize(
    "name",
    [
        "Alice Example",
        "N:Dubz",
        "C:Drive",
        "c:3po",
        "C: Drive Team",
        "N:L0 team",
        "C:SPEAKER",
        "Sam: Finance",
    ],
)
def test_a_real_name_that_looks_like_a_label_is_not_a_placeholder(name):
    assert not is_placeholder_speaker_name(name)
