from django.test import override_settings

from hattori.compatibility.files import need_to_fix_request_files
from hattori.conf import settings
from hattori.params.models import FileModel


def test_nested_settings_overrides_update_existing_reference_and_restore():
    original = settings.FIX_REQUEST_FILES_METHODS
    with override_settings(HATTORI_FIX_REQUEST_FILES_METHODS={"PUT"}):
        assert settings.FIX_REQUEST_FILES_METHODS == {"PUT"}
        with override_settings(HATTORI_FIX_REQUEST_FILES_METHODS=set()):
            assert settings.FIX_REQUEST_FILES_METHODS == set()
        assert settings.FIX_REQUEST_FILES_METHODS == {"PUT"}
    assert settings.FIX_REQUEST_FILES_METHODS == original


def test_unrelated_settings_leave_hattori_settings_untouched():
    original = settings.FIX_REQUEST_FILES_METHODS
    with override_settings(DEBUG=False):
        assert settings.FIX_REQUEST_FILES_METHODS is original


def test_file_registration_check_uses_current_settings():
    with override_settings(MIDDLEWARE=[]):
        assert need_to_fix_request_files(["PATCH"], [FileModel])
        with override_settings(HATTORI_FIX_REQUEST_FILES_METHODS={"PUT"}):
            assert not need_to_fix_request_files(["PATCH"], [FileModel])
            assert need_to_fix_request_files(["PUT"], [FileModel])
        assert need_to_fix_request_files(["PATCH"], [FileModel])
