"""Test doubles: no Neo4j, Groq or model downloads are needed."""

from types import SimpleNamespace


class FakeDB:
    """Records queries; ``responder(query, params)`` supplies the rows."""

    def __init__(self, responder=None):
        self.responder = responder or (lambda query, params: [])
        self.queries = []
        self.transactions = []

    def verify_connection(self):
        return True

    def run_query(self, query, parameters=None):
        self.queries.append((query, parameters or {}))
        return self.responder(query, parameters or {})

    def run_transaction(self, statements):
        self.transactions.append(list(statements))
        return [[] for _ in statements]

    def close(self):
        pass


class FakeGroqClient:
    """Mimics ``client.chat.completions.create`` with scripted outcomes."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **request):
        self.requests.append(request)
        outcome = self.outcomes.pop(0)

        if isinstance(outcome, Exception):
            raise outcome

        content, finish = outcome if isinstance(outcome, tuple) else (outcome, "stop")
        message = SimpleNamespace(content=content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message,
                                                        finish_reason=finish)])


class HttpError(Exception):
    def __init__(self, status_code):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


class ScriptedGenerator:
    """Stands in for GroqGenerator in extractor/pipeline tests."""

    model = "fake-model"

    def __init__(self, responses=(), answer="An answer [1]."):
        self.responses = list(responses)
        self.calls = []
        self.answer = answer

    def complete(self, messages, temperature=0.2, max_tokens=2000):
        self.calls.append(messages)
        return self.responses.pop(0) if self.responses else ""

    def generate_answer(self, question, chunks):
        self.calls.append((question, chunks))
        return self.answer
