import urllib.request

import pytest


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Aucun test ne doit appeler Uber Eats, Deliveroo ou le géocodeur pour de vrai."""
    def refuse(*args, **kwargs):
        raise RuntimeError("accès réseau interdit pendant les tests")
    monkeypatch.setattr(urllib.request, "urlopen", refuse)
