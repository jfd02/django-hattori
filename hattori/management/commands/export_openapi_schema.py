from pathlib import Path
from typing import Any

import orjson
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.urls.base import resolve
from django.utils.module_loading import import_string

from hattori.main import HattoriAPI
from hattori.management.utils import command_docstring
from hattori.responses import JSON_OPT, json_default


class Command(BaseCommand):
    """
    Example:

        ```terminal
        python manage.py export_openapi_schema
        ```

        ```terminal
        python manage.py export_openapi_schema --api project.urls.api
        ```
    """

    help = "Exports Open API schema"

    def _get_api_instance(self, api_path: str | None = None) -> HattoriAPI:
        if not api_path:
            try:
                return resolve("/api/").func.keywords["api"]  # type: ignore
            except AttributeError:
                raise CommandError(
                    "No HattoriAPI instance found; please specify one with --api"
                ) from None

        try:
            api = import_string(api_path)
        except ImportError:
            raise CommandError(
                f"Module or attribute for {api_path} not found!"
            ) from None

        if not isinstance(api, HattoriAPI):
            raise CommandError(f"{api_path} is not instance of HattoriAPI!")

        return api

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--api",
            dest="api",
            default=None,
            type=str,
            help="Specify api instance module",
        )
        parser.add_argument(
            "--output",
            dest="output",
            default=None,
            type=str,
            help="Output schema to a file (outputs to stdout if omitted).",
        )
        parser.add_argument(
            "--indent",
            dest="indent",
            default=False,
            action="store_true",
            help="Indent JSON output",
        )
        parser.add_argument(
            "--sorted",
            dest="sort_keys",
            default=False,
            action="store_true",
            help="Sort JSON keys",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        api = self._get_api_instance(options["api"])
        schema = api.get_openapi_schema()
        opt = JSON_OPT
        if options["indent"]:
            opt |= orjson.OPT_INDENT_2
        if options["sort_keys"]:
            opt |= orjson.OPT_SORT_KEYS
        result = orjson.dumps(schema, default=json_default, option=opt)

        if options["output"]:
            with Path(options["output"]).open("wb") as f:
                f.write(result)
        else:
            self.stdout.write(result.decode())


__doc__ = command_docstring(Command)
