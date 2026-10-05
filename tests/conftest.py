import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.resolve()

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests/demo_project"))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "demo.settings")

import django  # noqa
from hypothesis import settings  # noqa

django.setup()

# Keep ordinary CI runs bounded; opt into a larger search from the command line.
settings.register_profile("openapi", max_examples=40, deadline=None)
settings.register_profile(
    "openapi-deep", parent=settings.get_profile("openapi"), max_examples=500
)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "openapi"))

# Opt in to the shipped test-client fixtures (hattori_client / hattori_async_client).
pytest_plugins = ["hattori.testing.plugin"]


def pytest_generate_tests(metafunc):
    os.environ["HATTORI_SKIP_REGISTRY"] = "yes"
