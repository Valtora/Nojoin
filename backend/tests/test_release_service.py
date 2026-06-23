from backend.api.services.release_service import (
    ReleaseInfo,
    compare_versions,
    determine_update_status,
    get_release_by_version,
    normalise_version,
)


def test_normalise_version_accepts_semver_with_optional_v_prefix():
    assert normalise_version("0.7.6") == "0.7.6"
    assert normalise_version("v0.7.6") == "0.7.6"


def test_normalise_version_rejects_non_release_tags():
    assert normalise_version("latest") is None
    assert normalise_version("sha-0a93da3") is None


def test_compare_versions_uses_semver_ordering():
    assert compare_versions("0.7.5", "0.7.6") == -1
    assert compare_versions("0.7.6", "0.7.6") == 0
    assert compare_versions("0.7.7", "0.7.6") == 1


def test_determine_update_status_handles_ahead_versions():
    assert determine_update_status("0.7.5", "0.7.6") == "update-available"
    assert determine_update_status("0.7.6", "0.7.6") == "current"
    assert determine_update_status("0.7.7", "0.7.6") == "ahead"
    assert determine_update_status("dev", "0.7.6") == "unknown"


def test_release_helpers_find_matching_release():
    release = ReleaseInfo(
        version="0.7.6",
        tag_name="v0.7.6",
        name="v0.7.6",
        html_url="https://example.invalid/release/v0.7.6",
        body="Release notes",
    )

    assert get_release_by_version([release], "v0.7.6") == release
