from __future__ import annotations

from pathlib import Path

from scrapperlanas import create_app
from scrapperlanas.services.ai import LocalAiAssistant


class DummyResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self.payload


def test_ai_enrichment_uses_best_available_model(monkeypatch, tmp_path: Path):
    app = create_app(
        {
            "TESTING": True,
            "DATABASE": str(tmp_path / "ai-test.sqlite3"),
            "OLLAMA_ENABLED": True,
            "OLLAMA_MODEL": "llama3.1:8b",
            "OLLAMA_EMBED_MODEL": "nomic-embed-text:latest",
            "OLLAMA_MODEL_PREFERENCES": ("mistral:latest", "qwen2.5-coder:7b"),
        }
    )

    def fake_get(url, timeout):
        assert url.endswith("/api/tags")
        return DummyResponse(
            {
                "models": [
                    {"name": "mistral:latest"},
                    {"name": "qwen2.5-coder:7b"},
                    {"name": "nomic-embed-text:latest"},
                ]
            }
        )

    def fake_post(url, json, timeout):
        if url.endswith("/api/embed"):
            payload = json["input"]
            if "Ideal freelance opportunities" in payload:
                return DummyResponse({"embeddings": [[0.9, 0.1, 0.0]]})
            return DummyResponse({"embeddings": [[0.8, 0.2, 0.0]]})

        if url.endswith("/api/generate"):
            assert json["model"] == "mistral:latest"
            return DummyResponse(
                {
                    "response": (
                        '{"summary":"Proyecto fuerte para scraping y automatizacion.",'
                        '"suggested_reply":"Hola, puedo tomar el proyecto y proponer alcance, hitos y entregables.",'
                        '"sector":"Data","stack":["Python","Docker"],'
                        '"fit_label":"strong_fit","fit_reason":"Muy alineado con scraping y backend.",'
                        '"recommended_action":"apply_now","recommended_state":"INTERESANTE",'
                        '"confidence":88,"score_delta":9,'
                        '"missing_info":"Confirmar deadline y acceso a fuentes."}'
                    )
                }
            )

        raise AssertionError(f"Unexpected url: {url}")

    monkeypatch.setattr("scrapperlanas.services.ai.requests.get", fake_get)
    monkeypatch.setattr("scrapperlanas.services.ai.requests.post", fake_post)

    with app.app_context():
        assistant = LocalAiAssistant()
        payload = assistant.enrich(
            title="Python scraping project",
            raw_text="Need a Python scraper and backend API for B2B lead generation.",
            stack=["Python"],
            budget_text="USD 2400",
            source_label="Demo Feed Local",
            risk_level="low",
            sector="",
            preferred_keywords=("python", "scraping", "automation"),
            min_budget=1500,
            base_score=60,
        )

    assert payload["model_used"] == "mistral:latest"
    assert payload["sector"] == "Data"
    assert payload["fit_label"] == "strong_fit"
    assert payload["recommended_action"] == "apply_now"
    assert payload["recommended_state"] == "INTERESANTE"
    assert payload["semantic_score"] is not None
    assert payload["score_delta"] > 0


def test_ai_fallback_without_ollama(tmp_path: Path):
    app = create_app(
        {
            "TESTING": True,
            "DATABASE": str(tmp_path / "ai-fallback.sqlite3"),
            "OLLAMA_ENABLED": False,
        }
    )

    with app.app_context():
        assistant = LocalAiAssistant()
        payload = assistant.enrich(
            title="Automatizacion n8n para CRM",
            raw_text="Buscamos freelance para automatizar flujos con n8n y APIs.",
            stack=[],
            budget_text="",
            preferred_keywords=("n8n", "automation"),
            min_budget=0,
        )

    assert payload["model_used"] == "heuristic"
    assert payload["fit_label"] in {"possible_fit", "strong_fit", "weak_fit"}
    assert payload["recommended_action"] in {"apply_now", "review_today", "clarify_scope", "skip"}


def test_ai_enrichment_uses_gemini(monkeypatch, tmp_path: Path):
    app = create_app(
        {
            "TESTING": True,
            "DATABASE": str(tmp_path / "ai-gemini.sqlite3"),
            "AI_PROVIDER": "gemini",
            "GEMINI_API_KEY": "gem-test-key",
            "GEMINI_MODEL": "gemini-2.5-flash-lite",
            "AI_EMBED_PROVIDER": "gemini",
            "GEMINI_EMBED_MODEL": "gemini-embedding-001",
        }
    )

    def fake_post(url, json, timeout, headers=None, params=None):
        if url.endswith(":embedContent"):
            text = json["content"]["parts"][0]["text"]
            if "Ideal freelance opportunities" in text:
                return DummyResponse({"embedding": {"values": [0.9, 0.1, 0.0]}})
            return DummyResponse({"embedding": {"values": [0.8, 0.2, 0.0]}})

        if url.endswith(":generateContent"):
            assert params == {"key": "gem-test-key"}
            return DummyResponse(
                {
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {
                                        "text": (
                                            '{"summary":"Proyecto fuerte para scraping y automatizacion.",'
                                            '"suggested_reply":"Hola, puedo tomar el proyecto y proponer alcance, hitos y entregables.",'
                                            '"sector":"Data","stack":["Python","Docker"],'
                                            '"fit_label":"strong_fit","fit_reason":"Muy alineado con scraping y backend.",'
                                            '"recommended_action":"apply_now","recommended_state":"INTERESANTE",'
                                            '"confidence":88,"score_delta":9,'
                                            '"missing_info":"Confirmar deadline y acceso a fuentes."}'
                                        )
                                    }
                                ]
                            }
                        }
                    ]
                }
            )

        raise AssertionError(f"Unexpected url: {url}")

    monkeypatch.setattr("scrapperlanas.services.ai.requests.post", fake_post)

    with app.app_context():
        assistant = LocalAiAssistant()
        payload = assistant.enrich(
            title="Python scraping project",
            raw_text="Need a Python scraper and backend API for B2B lead generation.",
            stack=["Python"],
            budget_text="USD 2400",
            source_label="Demo Feed Local",
            risk_level="low",
            sector="",
            preferred_keywords=("python", "scraping", "automation"),
            min_budget=1500,
            base_score=60,
        )

    assert payload["model_used"] == "gemini-2.5-flash-lite"
    assert payload["sector"] == "Data"
    assert payload["fit_label"] == "strong_fit"
    assert payload["recommended_action"] == "apply_now"
    assert payload["recommended_state"] == "INTERESANTE"
    assert payload["semantic_score"] is not None


def test_ai_enrichment_uses_deepseek(monkeypatch, tmp_path: Path):
    app = create_app(
        {
            "TESTING": True,
            "DATABASE": str(tmp_path / "ai-deepseek.sqlite3"),
            "AI_PROVIDER": "deepseek",
            "DEEPSEEK_API_KEY": "ds-test-key",
            "DEEPSEEK_MODEL": "deepseek-chat",
        }
    )

    def fake_post(url, json, timeout, headers=None):
        if url.endswith("/chat/completions"):
            assert headers["Authorization"] == "Bearer ds-test-key"
            return DummyResponse(
                {
                    "choices": [
                        {
                            "message": {
                                "content": (
                                    '{"summary":"Proyecto viable para backend y scraping.",'
                                    '"suggested_reply":"Hola, puedo ayudar con el backend y el scraper con entregables claros.",'
                                    '"sector":"Data","stack":["Python","PostgreSQL"],'
                                    '"fit_label":"possible_fit","fit_reason":"Buen match tecnico.",'
                                    '"recommended_action":"review_today","recommended_state":"VISTO",'
                                    '"confidence":76,"score_delta":5,'
                                    '"missing_info":"Confirmar alcance final y deadline."}'
                                )
                            }
                        }
                    ]
                }
            )

        raise AssertionError(f"Unexpected url: {url}")

    monkeypatch.setattr("scrapperlanas.services.ai.requests.post", fake_post)

    with app.app_context():
        assistant = LocalAiAssistant()
        payload = assistant.enrich(
            title="Python backend project",
            raw_text="Need backend support and a small scraper for internal ops.",
            stack=["Python"],
            budget_text="USD 1800",
            source_label="Demo Feed Local",
            risk_level="low",
            sector="",
            preferred_keywords=("python", "scraping", "automation"),
            min_budget=1000,
            base_score=52,
        )

    assert payload["model_used"] == "deepseek-chat"
    assert payload["fit_label"] == "possible_fit"
    assert payload["recommended_state"] == "VISTO"
    assert payload["semantic_score"] is None
