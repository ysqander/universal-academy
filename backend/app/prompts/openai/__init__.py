"""The OpenAI prompt set: every instruction text the app sends to GPT, assembled from the shared
rules (app/prompts/rules.py) and this provider's own wording. app.prompts.for_role(role) picks the set of the
provider that runs the role."""

from ..rules import *  # noqa: F401,F403 — the shared rules are part of every set
from .core import *  # noqa: F401,F403
from .ingest import *  # noqa: F401,F403
from .proof import *  # noqa: F401,F403
from .v2 import *  # noqa: F401,F403
