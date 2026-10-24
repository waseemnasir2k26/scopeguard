import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FIX = ROOT / "tests" / "fixtures"
EXAMPLES = ROOT / "examples"


@pytest.fixture
def fix():
    return FIX


@pytest.fixture
def examples():
    return EXAMPLES


class FakeLLM:
    """Scriptable LLM: replies with queued strings (or a function), records every prompt."""
    name = "fake"
    model = "fake-1"
    is_cloud = False

    def __init__(self, replies=None, fn=None):
        self.replies = list(replies or [])
        self.fn = fn
        self.prompts = []

    def label(self):
        return "fake:fake-1"

    def chat(self, system, user, schema=None):
        self.prompts.append(user)
        if self.fn:
            return self.fn(system, user)
        return self.replies.pop(0)

    def json_call(self, system, user, schema=None):
        from scopeguard.llm import Backend
        return Backend.json_call(self, system, user, schema)
