import json
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from hattori.management.commands.export_openapi_schema import Command as ExportCmd


def test_export_default():
    output = StringIO()
    call_command(ExportCmd(), stdout=output)
    json.loads(output.getvalue())  # if no exception, then OK
    assert len(output.getvalue().splitlines()) == 1


def test_export_indent():
    output = StringIO()
    call_command(ExportCmd(), indent=1, stdout=output)
    assert len(output.getvalue().splitlines()) > 1


def test_export_to_file():
    with tempfile.TemporaryDirectory() as tmp:
        output_file = Path(tmp) / "result.json"
        call_command(ExportCmd(), output=output_file)
        json.loads(Path(output_file).read_text())


def test_export_custom():
    with pytest.raises(CommandError):
        call_command(ExportCmd(), api="something.that.doesnotexist")

    with pytest.raises(CommandError) as e:
        call_command(ExportCmd(), api="django.core.management.base.BaseCommand")
    assert (
        str(e.value)
        == "django.core.management.base.BaseCommand is not instance of HattoriAPI!"
    )

    call_command(ExportCmd(), api="demo.urls.api_v1")
    call_command(ExportCmd(), api="demo.urls.api_v2")


@patch("hattori.management.commands.export_openapi_schema.resolve")
def test_export_default_without_api_endpoint(mock):
    mock.side_effect = AttributeError()
    output = StringIO()
    with pytest.raises(CommandError) as e:
        call_command(ExportCmd(), stdout=output)
    assert str(e.value) == "No HattoriAPI instance found; please specify one with --api"


@patch("hattori.management.commands.export_openapi_schema.resolve")
def test_export_default_endpoint_missing_api_keyword(mock):
    # /api/ resolves but its view has no "api" keyword -> KeyError, not AttributeError.
    from unittest.mock import Mock

    mock.return_value = Mock(func=Mock(keywords={}))
    with pytest.raises(CommandError) as e:
        call_command(ExportCmd(), stdout=StringIO())
    assert str(e.value) == "No HattoriAPI instance found; please specify one with --api"


@patch("hattori.management.commands.export_openapi_schema.resolve")
def test_export_default_endpoint_not_resolvable(mock):
    # /api/ is not a registered route -> Resolver404.
    from django.urls import Resolver404

    mock.side_effect = Resolver404()
    with pytest.raises(CommandError) as e:
        call_command(ExportCmd(), stdout=StringIO())
    assert str(e.value) == "No HattoriAPI instance found; please specify one with --api"


def test_command_docstring_groups_command_args_before_base_args():
    """The command's own options must be listed before inherited Django base options."""
    from hattori.management.utils import command_docstring

    doc = command_docstring(ExportCmd)
    names = [
        line.strip().split(":", 1)[0].split(" (")[0]
        for line in doc.splitlines()
        if line.startswith("    ") and ":" in line
    ]
    command_args = {"--api", "--output", "--indent", "--sorted"}
    cmd_positions = [i for i, n in enumerate(names) if n in command_args]
    base_positions = [i for i, n in enumerate(names) if n not in command_args]
    assert cmd_positions and base_positions, names
    # Every command-specific arg comes before every base arg.
    assert max(cmd_positions) < min(base_positions), names
