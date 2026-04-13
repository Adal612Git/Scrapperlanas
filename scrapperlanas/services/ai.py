from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable

import requests
from flask import current_app


AI_PROVIDERS = {"heuristic", "ollama", "gemini", "deepseek"}
EMBED_PROVIDERS = {"none", "ollama", "gemini"}

TECH_KEYWORDS = (
    "python",
    "scraping",
    "n8n",
    "rust",
    "flask",
    "fastapi",
    "postgresql",
    "beautifulsoup",
    "requests",
    "sql",
    "react",
    "javascript",
    "typescript",
    "docker",
    "llm",
    "ollama",
    "api",
    "automation",
)

TECH_LABELS = {
    "api": "API",
    "beautifulsoup": "BeautifulSoup",
    "docker": "Docker",
    "fastapi": "FastAPI",
    "flask": "Flask",
    "javascript": "JavaScript",
    "llm": "LLM",
    "n8n": "n8n",
    "ollama": "Ollama",
    "postgresql": "PostgreSQL",
    "python": "Python",
    "react": "React",
    "requests": "Requests",
    "rust": "Rust",
    "sql": "SQL",
    "typescript": "TypeScript",
}

SECTOR_HINTS = {
    "FinTech": ("fintech", "payments", "banking", "billing", "cashflow"),
    "E-commerce": ("shopify", "ecommerce", "woocommerce", "catalog"),
    "SaaS": ("saas", "platform", "dashboard", "b2b"),
    "AI/ML": ("llm", "ai", "machine learning", "ollama"),
    "Data": ("etl", "analytics", "data warehouse", "scraping", "scraper"),
    "DevOps": ("docker", "kubernetes", "cloud", "ci/cd"),
}

FIT_LABELS = {"strong_fit", "possible_fit", "weak_fit", "avoid"}
ACTION_LABELS = {"apply_now", "review_today", "clarify_scope", "skip"}
PIPELINE_STATE_HINTS = {
    "NUEVO",
    "VISTO",
    "INTERESANTE",
    "RESPONDIDO",
    "APLICADO",
    "FOLLOW_UP",
    "GANADO",
    "PERDIDO",
    "SOSPECHOSO",
    "DESCARTADO",
}


class LocalAiAssistant:
    def __init__(self) -> None:
        self._available_ollama_models: set[str] | None = None
        self._ollama_reachable: bool | None = None
        self._generation_models: dict[str, str | None] = {}
        self._embedding_models: dict[str, str | None] = {}
        self._embedding_cache: dict[str, list[float]] = {}

    def enrich(
        self,
        *,
        title: str,
        raw_text: str,
        stack: list[str],
        budget_text: str,
        source_label: str = "",
        risk_level: str = "low",
        sector: str = "",
        preferred_keywords: Iterable[str] = (),
        min_budget: int = 0,
        base_score: int = 0,
        force_heuristic: bool = False,
    ) -> dict:
        preferred_keywords = tuple(
            keyword.strip().lower()
            for keyword in preferred_keywords
            if str(keyword).strip()
        )
        fallback = self._fallback_payload(
            title=title,
            raw_text=raw_text,
            stack=stack,
            budget_text=budget_text,
            sector=sector,
            preferred_keywords=preferred_keywords,
            min_budget=min_budget,
            risk_level=risk_level,
        )

        provider = "heuristic" if force_heuristic else self._resolve_provider()
        if provider == "heuristic":
            return fallback

        semantic_score = self._semantic_score(
            generation_provider=provider,
            title=title,
            raw_text=raw_text,
            preferred_keywords=preferred_keywords,
            min_budget=min_budget,
        )
        if semantic_score is not None:
            fallback["semantic_score"] = semantic_score
            fallback["fit_label"] = self._fit_label_from_semantic(semantic_score, fallback["fit_label"])
            fallback["score_delta"] = self._merge_score_delta(
                fallback["score_delta"],
                self._semantic_delta(semantic_score),
            )
            fallback["fit_reason"] = (
                f"{fallback['fit_reason']} Relevancia semantica estimada: {semantic_score}/100."
            ).strip()

        model_name = self._resolve_generation_model(provider)
        if not model_name:
            return fallback

        prompt = self._build_prompt(
            title=title,
            raw_text=raw_text,
            stack=stack,
            budget_text=budget_text,
            source_label=source_label,
            risk_level=risk_level,
            sector=sector,
            preferred_keywords=preferred_keywords,
            min_budget=min_budget,
            base_score=base_score,
            semantic_score=semantic_score,
            fallback=fallback,
        )

        try:
            raw_json = self._generate(prompt=prompt, provider=provider, model_name=model_name)
            parsed = self._extract_json(raw_json)
            if not parsed:
                return fallback
            return self._merge_analysis(
                fallback=fallback,
                parsed=parsed,
                model_name=model_name,
            )
        except Exception:
            return fallback

    def _resolve_provider(self) -> str:
        configured = str(current_app.config.get("AI_PROVIDER", "") or "").strip().lower()
        if configured == "ollama":
            return "ollama" if self._ollama_available() else "heuristic"
        if configured in AI_PROVIDERS and configured != "heuristic":
            return configured
        if current_app.config.get("AI_PROVIDER_EXPLICIT") and configured == "heuristic":
            return "heuristic"
        if current_app.config.get("OLLAMA_ENABLED"):
            return "ollama" if self._ollama_available() else "heuristic"
        if configured in AI_PROVIDERS:
            return configured
        return "heuristic"

    def _resolve_embed_provider(self, generation_provider: str) -> str:
        configured = str(current_app.config.get("AI_EMBED_PROVIDER", "") or "").strip().lower()
        if configured in EMBED_PROVIDERS:
            return configured
        if generation_provider in {"ollama", "gemini"}:
            return generation_provider
        return "none"

    def _provider_api_key(self, provider: str) -> str | None:
        generic = str(current_app.config.get("AI_API_KEY", "") or "").strip()
        if generic and provider in {"gemini", "deepseek"}:
            return generic
        if provider == "gemini":
            return current_app.config.get("GEMINI_API_KEY")
        if provider == "deepseek":
            return current_app.config.get("DEEPSEEK_API_KEY")
        return None

    def _provider_base_url(self, provider: str) -> str:
        generic = str(current_app.config.get("AI_BASE_URL", "") or "").strip()
        if generic:
            return generic
        if provider == "gemini":
            return str(current_app.config.get("GEMINI_BASE_URL", "") or "").strip()
        if provider == "deepseek":
            return str(current_app.config.get("DEEPSEEK_BASE_URL", "") or "").strip()
        return str(current_app.config.get("OLLAMA_URL", "") or "").strip()

    def _resolve_generation_model(self, provider: str) -> str | None:
        if provider in self._generation_models:
            return self._generation_models[provider]

        if provider == "ollama":
            model_name = self._resolve_ollama_generation_model()
        elif provider == "gemini":
            model_name = self._resolve_gemini_generation_model()
        elif provider == "deepseek":
            model_name = self._resolve_deepseek_generation_model()
        else:
            model_name = None

        self._generation_models[provider] = model_name
        return model_name

    def _resolve_ollama_generation_model(self) -> str | None:
        available = self._list_ollama_models()
        configured = str(current_app.config.get("AI_MODEL", "") or "").strip() or current_app.config.get("OLLAMA_MODEL")
        if configured and (not available or configured in available):
            return configured

        for candidate in current_app.config.get("OLLAMA_MODEL_PREFERENCES", ()):
            if candidate in available:
                return candidate

        return configured

    def _resolve_gemini_generation_model(self) -> str | None:
        configured = str(current_app.config.get("AI_MODEL", "") or "").strip()
        if configured:
            return configured
        return str(current_app.config.get("GEMINI_MODEL", "") or "").strip() or None

    def _resolve_deepseek_generation_model(self) -> str | None:
        configured = str(current_app.config.get("AI_MODEL", "") or "").strip()
        if configured:
            return configured
        return str(current_app.config.get("DEEPSEEK_MODEL", "") or "").strip() or None

    def _resolve_embedding_model(self, provider: str) -> str | None:
        if provider in self._embedding_models:
            return self._embedding_models[provider]

        if provider == "ollama":
            model_name = self._resolve_ollama_embedding_model()
        elif provider == "gemini":
            model_name = self._resolve_gemini_embedding_model()
        else:
            model_name = None

        self._embedding_models[provider] = model_name
        return model_name

    def _resolve_ollama_embedding_model(self) -> str | None:
        available = self._list_ollama_models()
        configured = str(current_app.config.get("AI_EMBED_MODEL", "") or "").strip() or current_app.config.get(
            "OLLAMA_EMBED_MODEL"
        )
        if configured and (not available or configured in available):
            return configured

        for candidate in ("nomic-embed-text:latest", "mxbai-embed-large:latest"):
            if candidate in available:
                return candidate

        return None

    def _resolve_gemini_embedding_model(self) -> str | None:
        configured = str(current_app.config.get("AI_EMBED_MODEL", "") or "").strip()
        if configured:
            return configured
        return str(current_app.config.get("GEMINI_EMBED_MODEL", "") or "").strip() or None

    def _fallback_payload(
        self,
        *,
        title: str,
        raw_text: str,
        stack: list[str],
        budget_text: str,
        sector: str,
        preferred_keywords: tuple[str, ...],
        min_budget: int,
        risk_level: str,
    ) -> dict:
        clean_text = " ".join((raw_text or "").split())
        summary = clean_text[:260].strip()
        if len(clean_text) > 260:
            summary += "..."

        detected_stack = stack or self._detect_stack(clean_text)
        detected_sector = sector or self._detect_sector(clean_text)
        fit_label = self._fit_label_from_text(clean_text, preferred_keywords, risk_level)
        fit_reason = self._fit_reason(clean_text, preferred_keywords, min_budget, budget_text, fit_label)
        recommended_action = self._default_action(fit_label, risk_level)
        recommended_state = self._default_state(fit_label, risk_level)
        budget_hint = budget_text or "presupuesto por confirmar"
        summary = summary or title
        reply = (
            f"Hola, vi la oportunidad '{title}'. "
            f"Puedo cubrirla con experiencia en {', '.join(detected_stack[:3]) or 'backend y automatizacion'}, "
            f"proponiendo alcance, entregables y tiempos claros dentro de {budget_hint}."
        )

        return {
            "summary": summary,
            "suggested_reply": reply,
            "sector": detected_sector,
            "stack": detected_stack,
            "fit_label": fit_label,
            "fit_reason": fit_reason,
            "recommended_action": recommended_action,
            "recommended_state": recommended_state,
            "confidence": 54,
            "score_delta": self._fit_delta(fit_label),
            "semantic_score": None,
            "missing_info": "Validar alcance, entregables, fecha limite y canal de contacto.",
            "model_used": "heuristic",
        }

    def _list_ollama_models(self) -> set[str]:
        if self._available_ollama_models is not None:
            return self._available_ollama_models

        try:
            response = requests.get(
                f"{self._provider_base_url('ollama').rstrip('/')}/api/tags",
                timeout=self._ollama_timeout(),
            )
            response.raise_for_status()
            payload = response.json()
            models = payload.get("models", []) or []
            self._ollama_reachable = True
            self._available_ollama_models = {
                str(model.get("name", "")).strip()
                for model in models
                if model.get("name")
            }
        except Exception:
            self._ollama_reachable = False
            self._available_ollama_models = set()
        return self._available_ollama_models

    def _semantic_score(
        self,
        *,
        generation_provider: str,
        title: str,
        raw_text: str,
        preferred_keywords: tuple[str, ...],
        min_budget: int,
    ) -> int | None:
        embed_provider = self._resolve_embed_provider(generation_provider)
        if embed_provider == "none":
            return None

        model_name = self._resolve_embedding_model(embed_provider)
        if not model_name:
            return None

        profile_text = self._target_profile_text(preferred_keywords, min_budget)
        document_text = f"{title}\n{raw_text[:2600]}".strip()
        if not document_text:
            return None

        try:
            opportunity_vector = self._embed_text(
                text=document_text,
                provider=embed_provider,
                model_name=model_name,
            )
            target_vector = self._embed_text(
                text=profile_text,
                provider=embed_provider,
                model_name=model_name,
            )
        except Exception:
            return None

        similarity = self._cosine_similarity(opportunity_vector, target_vector)
        return max(0, min(100, round(similarity * 100)))

    def _embed_text(self, *, text: str, provider: str, model_name: str) -> list[float]:
        cache_key = f"{provider}:{model_name}:{hash(text)}"
        if cache_key in self._embedding_cache:
            return self._embedding_cache[cache_key]

        if provider == "ollama":
            vector = self._embed_text_ollama(text, model_name)
        elif provider == "gemini":
            vector = self._embed_text_gemini(text, model_name)
        else:
            raise RuntimeError(f"Unsupported embedding provider: {provider}")

        self._embedding_cache[cache_key] = vector
        return vector

    def _embed_text_ollama(self, text: str, model_name: str) -> list[float]:
        base_url = self._provider_base_url("ollama").rstrip("/")
        endpoints = (
            (
                f"{base_url}/api/embed",
                {"model": model_name, "input": text[:4000]},
                "embeddings",
            ),
            (
                f"{base_url}/api/embeddings",
                {"model": model_name, "prompt": text[:4000]},
                "embedding",
            ),
        )

        for url, payload, key in endpoints:
            try:
                response = requests.post(
                    url,
                    json=payload,
                    timeout=self._ollama_timeout(),
                )
                response.raise_for_status()
                body = response.json()
                if key == "embeddings":
                    vectors = body.get("embeddings") or []
                    if vectors:
                        return vectors[0]
                else:
                    vector = body.get("embedding") or []
                    if vector:
                        return vector
            except Exception:
                continue

        raise RuntimeError("Unable to fetch embedding from Ollama.")

    def _embed_text_gemini(self, text: str, model_name: str) -> list[float]:
        api_key = self._provider_api_key("gemini")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is required for Gemini embeddings.")

        response = requests.post(
            f"{self._provider_base_url('gemini').rstrip('/')}/models/{model_name}:embedContent",
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": api_key,
            },
            json={
                "content": {"parts": [{"text": text[:4000]}]},
                "taskType": "SEMANTIC_SIMILARITY",
            },
            timeout=current_app.config["REQUEST_TIMEOUT_SECONDS"],
        )
        response.raise_for_status()
        payload = response.json()

        if payload.get("embedding", {}).get("values"):
            return payload["embedding"]["values"]

        embeddings = payload.get("embeddings") or []
        if embeddings and embeddings[0].get("values"):
            return embeddings[0]["values"]

        raise RuntimeError("Unable to fetch embedding from Gemini.")

    def _build_prompt(
        self,
        *,
        title: str,
        raw_text: str,
        stack: list[str],
        budget_text: str,
        source_label: str,
        risk_level: str,
        sector: str,
        preferred_keywords: tuple[str, ...],
        min_budget: int,
        base_score: int,
        semantic_score: int | None,
        fallback: dict,
    ) -> str:
        preferred = ", ".join(preferred_keywords or current_app.config["PREFERRED_KEYWORDS"])
        semantic_text = semantic_score if semantic_score is not None else "sin embedding"
        return (
            "Eres un analista senior de oportunidades freelance para un operador tecnico que resuelve automatizacion, scraping, backend, integraciones, CRM, pipelines de datos e IA aplicada.\n"
            "Evalua la oportunidad con criterio conservador, evita alucinar y responde SOLO JSON valido.\n"
            "Las claves exactas son: summary, suggested_reply, sector, stack, fit_label, fit_reason, "
            "recommended_action, recommended_state, confidence, score_delta, missing_info.\n"
            "Reglas:\n"
            "- summary: maximo 280 caracteres, en espanol, debe explicar el problema real y el tipo de entrega.\n"
            "- suggested_reply: maximo 420 caracteres, en espanol, como respuesta profesional del freelancer; breve, segura, sin humo y sin inventar experiencia.\n"
            "- sector: una sola categoria.\n"
            "- stack: arreglo de hasta 6 tecnologias con las tecnologias mas relevantes.\n"
            "- fit_label: strong_fit, possible_fit, weak_fit o avoid.\n"
            "- fit_reason: debe decir por que encaja o no encaja, mencionando stack, complejidad, presupuesto, tipo de entrega o ruido, no frases genericas.\n"
            "- recommended_action: apply_now, review_today, clarify_scope o skip.\n"
            "- recommended_state: usa un estado valido del pipeline y que tenga sentido operativo.\n"
            "- confidence: entero 0-100.\n"
            "- score_delta: entero entre -20 y 20, ajusta el score si la oportunidad es claramente mejor o peor que el score base.\n"
            "- missing_info: una sola frase corta con la pieza concreta que falta para decidir.\n"
            "Contexto del operador ideal:\n"
            f"- Preferencias clave: {preferred}\n"
            f"- Presupuesto minimo deseado: {min_budget or 0}\n"
            f"- Heuristica previa: score={base_score}, fit={fallback['fit_label']}, accion={fallback['recommended_action']}\n"
            f"- Relevancia semantica estimada: {semantic_text}\n"
            f"- Fuente: {source_label or 'desconocida'}\n"
            f"- Riesgo de fuente: {risk_level}\n"
            f"- Sector detectado: {sector or fallback['sector']}\n"
            f"- Stack detectado: {', '.join(stack or fallback['stack']) or 'no detectado'}\n"
            f"- Presupuesto: {budget_text or 'No especificado'}\n"
            "Criterio de veredicto:\n"
            "- strong_fit si el trabajo encaja con automatizacion, scraping, backend, integraciones, CRM, n8n, APIs, data pipelines o IA aplicada.\n"
            "- possible_fit si hay parte tecnica util pero el alcance es mixto o falta claridad.\n"
            "- weak_fit si es tecnico pero se aleja del foco real del operador o suena mas a mantenimiento generico.\n"
            "- avoid si es empleo formal, frontend puro, diseno puro, marketing puro o ruido claro.\n"
            "Criterio de plan de ataque:\n"
            "- prioriza como se resolveria de forma tecnica y concreta, con entregables y validacion.\n"
            "- menciona stack y enfoque real cuando aporte valor.\n"
            "Criterio de respuesta sugerida:\n"
            "- responde con confianza profesional, breve y natural.\n"
            "- confirma capacidad tecnica sin inventar experiencia falsa.\n"
            "- si hay ambiguedad, pide una aclaracion concreta.\n"
            f"TITULO: {title}\n"
            f"TEXTO:\n{raw_text[:3200]}"
        )

    def _generate(self, *, prompt: str, provider: str, model_name: str) -> str:
        if provider == "ollama":
            return self._generate_ollama(prompt, model_name)
        if provider == "gemini":
            return self._generate_gemini(prompt, model_name)
        if provider == "deepseek":
            return self._generate_deepseek(prompt, model_name)
        raise RuntimeError(f"Unsupported AI provider: {provider}")

    def _generate_ollama(self, prompt: str, model_name: str) -> str:
        response = requests.post(
            f"{self._provider_base_url('ollama').rstrip('/')}/api/generate",
            json={
                "model": model_name,
                "prompt": prompt,
                "stream": False,
                "format": "json",
                "options": {
                    "temperature": current_app.config["AI_TEMPERATURE"],
                    "num_ctx": current_app.config["OLLAMA_NUM_CTX"],
                },
            },
            timeout=self._ollama_timeout(),
        )
        response.raise_for_status()
        payload = response.json()
        return str(payload.get("response", "")).strip()

    def _ollama_available(self) -> bool:
        self._list_ollama_models()
        return bool(self._ollama_reachable)

    def _ollama_timeout(self) -> int:
        return int(current_app.config.get("OLLAMA_REQUEST_TIMEOUT_SECONDS") or 4)

    def _generate_gemini(self, prompt: str, model_name: str) -> str:
        api_key = self._provider_api_key("gemini")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is required for Gemini.")

        response = requests.post(
            f"{self._provider_base_url('gemini').rstrip('/')}/models/{model_name}:generateContent",
            params={"key": api_key},
            json={
                "contents": [
                    {
                        "parts": [
                            {"text": prompt},
                        ]
                    }
                ],
                "generationConfig": {
                    "temperature": current_app.config["AI_TEMPERATURE"],
                    "max_output_tokens": current_app.config["AI_MAX_OUTPUT_TOKENS"],
                    "response_mime_type": "application/json",
                    "response_schema": self._response_json_schema(),
                },
            },
            timeout=max(current_app.config["REQUEST_TIMEOUT_SECONDS"], 45),
        )
        response.raise_for_status()
        payload = response.json()
        return self._extract_gemini_text(payload)

    def _generate_deepseek(self, prompt: str, model_name: str) -> str:
        api_key = self._provider_api_key("deepseek")
        if not api_key:
            raise RuntimeError("DEEPSEEK_API_KEY is required for DeepSeek.")

        response = requests.post(
            f"{self._provider_base_url('deepseek').rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model_name,
                "messages": [
                    {
                        "role": "system",
                        "content": "Responde solo con JSON valido y sin texto adicional.",
                    },
                    {
                        "role": "user",
                        "content": prompt,
                    },
                ],
                "response_format": {"type": "json_object"},
                "temperature": current_app.config["AI_TEMPERATURE"],
                "max_tokens": current_app.config["AI_MAX_OUTPUT_TOKENS"],
                "stream": False,
            },
            timeout=max(current_app.config["REQUEST_TIMEOUT_SECONDS"], 45),
        )
        response.raise_for_status()
        payload = response.json()
        choices = payload.get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message", {})
        return str(message.get("content", "")).strip()

    def _extract_gemini_text(self, payload: dict) -> str:
        for candidate in payload.get("candidates") or []:
            content = candidate.get("content", {})
            for part in content.get("parts") or []:
                text = str(part.get("text", "")).strip()
                if text:
                    return text
        return ""

    def _response_json_schema(self) -> dict:
        return {
            "type": "OBJECT",
            "properties": {
                "summary": {"type": "STRING"},
                "suggested_reply": {"type": "STRING"},
                "sector": {"type": "STRING"},
                "stack": {
                    "type": "ARRAY",
                    "items": {"type": "STRING"},
                },
                "fit_label": {
                    "type": "STRING",
                    "enum": sorted(FIT_LABELS),
                },
                "fit_reason": {"type": "STRING"},
                "recommended_action": {
                    "type": "STRING",
                    "enum": sorted(ACTION_LABELS),
                },
                "recommended_state": {
                    "type": "STRING",
                    "enum": sorted(PIPELINE_STATE_HINTS),
                },
                "confidence": {"type": "INTEGER"},
                "score_delta": {"type": "INTEGER"},
                "missing_info": {"type": "STRING"},
            },
            "required": [
                "summary",
                "suggested_reply",
                "sector",
                "stack",
                "fit_label",
                "fit_reason",
                "recommended_action",
                "recommended_state",
                "confidence",
                "score_delta",
                "missing_info",
            ],
        }

    def _merge_analysis(self, *, fallback: dict, parsed: dict, model_name: str) -> dict:
        stack = self._merge_stack(fallback["stack"], parsed.get("stack", []))
        fit_label = self._normalize_fit_label(parsed.get("fit_label")) or fallback["fit_label"]
        score_delta = self._merge_score_delta(
            fallback["score_delta"],
            self._clamp_int(parsed.get("score_delta"), minimum=-20, maximum=20, default=0),
        )
        semantic_score = fallback.get("semantic_score")
        if semantic_score is not None:
            score_delta = self._merge_score_delta(score_delta, self._semantic_delta(semantic_score))

        return {
            "summary": self._clean_text(parsed.get("summary"), fallback["summary"], max_len=280),
            "suggested_reply": self._select_reply(parsed.get("suggested_reply"), fallback["suggested_reply"]),
            "sector": self._merge_sector(parsed.get("sector"), fallback["sector"]),
            "stack": stack,
            "fit_label": fit_label,
            "fit_reason": self._clean_text(parsed.get("fit_reason"), fallback["fit_reason"], max_len=240),
            "recommended_action": self._normalize_action(parsed.get("recommended_action")) or fallback["recommended_action"],
            "recommended_state": self._normalize_state(parsed.get("recommended_state")) or fallback["recommended_state"],
            "confidence": self._clamp_int(parsed.get("confidence"), minimum=0, maximum=100, default=fallback["confidence"]),
            "score_delta": score_delta,
            "semantic_score": semantic_score,
            "missing_info": self._clean_text(parsed.get("missing_info"), fallback["missing_info"], max_len=180),
            "model_used": model_name,
        }

    def _fit_label_from_text(self, text: str, preferred_keywords: tuple[str, ...], risk_level: str) -> str:
        lowered = text.lower()
        matches = sum(keyword in lowered for keyword in preferred_keywords)
        matches += sum(keyword in lowered for keyword in TECH_KEYWORDS[:6])

        if risk_level == "high":
            return "avoid"
        if matches >= 5:
            return "strong_fit"
        if matches >= 3:
            return "possible_fit"
        return "weak_fit"

    def _fit_reason(
        self,
        text: str,
        preferred_keywords: tuple[str, ...],
        min_budget: int,
        budget_text: str,
        fit_label: str,
    ) -> str:
        matched = [keyword for keyword in preferred_keywords if keyword in text.lower()]
        if matched:
            return f"Coincide con preferencias clave ({', '.join(matched[:4])}) y cae en {fit_label}."
        if min_budget and not budget_text:
            return "No hay presupuesto visible para comparar contra el minimo deseado."
        return f"Match estimado como {fit_label} por stack, contexto y claridad del alcance."

    def _default_action(self, fit_label: str, risk_level: str) -> str:
        if risk_level == "high":
            return "skip"
        return {
            "strong_fit": "apply_now",
            "possible_fit": "review_today",
            "weak_fit": "clarify_scope",
            "avoid": "skip",
        }[fit_label]

    def _default_state(self, fit_label: str, risk_level: str) -> str:
        if risk_level == "high":
            return "SOSPECHOSO"
        return {
            "strong_fit": "INTERESANTE",
            "possible_fit": "VISTO",
            "weak_fit": "NUEVO",
            "avoid": "DESCARTADO",
        }[fit_label]

    def _detect_stack(self, text: str) -> list[str]:
        lowered = text.lower()
        found = [
            self._canonical_stack_label(keyword)
            for keyword in TECH_KEYWORDS
            if keyword in lowered
        ]
        return found[:6]

    def _detect_sector(self, text: str) -> str:
        lowered = text.lower()
        for sector, hints in SECTOR_HINTS.items():
            if any(hint in lowered for hint in hints):
                return sector
        return "General Tech"

    def _extract_json(self, raw_json: str) -> dict | None:
        try:
            return json.loads(raw_json)
        except Exception:
            pass

        match = re.search(r"\{.*\}", raw_json, re.DOTALL)
        if not match:
            return None

        try:
            return json.loads(match.group(0))
        except Exception:
            return None

    def _target_profile_text(self, preferred_keywords: tuple[str, ...], min_budget: int) -> str:
        keywords = ", ".join(preferred_keywords or current_app.config["PREFERRED_KEYWORDS"])
        return (
            "Ideal freelance opportunities involve Python scraping, data extraction, backend APIs, "
            "workflow automation, n8n, dashboards, PostgreSQL, operations, clear scope, remote work, "
            f"visible budget and these preferences: {keywords}. Preferred minimum budget: {min_budget or 0}."
        )

    def _cosine_similarity(self, left: list[float], right: list[float]) -> float:
        numerator = sum(a * b for a, b in zip(left, right, strict=False))
        left_norm = math.sqrt(sum(a * a for a in left))
        right_norm = math.sqrt(sum(b * b for b in right))
        if not left_norm or not right_norm:
            return 0.0
        return numerator / (left_norm * right_norm)

    def _fit_label_from_semantic(self, semantic_score: int, fallback_label: str) -> str:
        if semantic_score >= 78:
            return "strong_fit"
        if semantic_score >= 62:
            return "possible_fit"
        if semantic_score >= 45:
            return "weak_fit"
        if fallback_label == "avoid":
            return "avoid"
        return "weak_fit"

    def _semantic_delta(self, semantic_score: int) -> int:
        if semantic_score >= 85:
            return 9
        if semantic_score >= 72:
            return 6
        if semantic_score >= 58:
            return 3
        if semantic_score >= 45:
            return 0
        if semantic_score >= 35:
            return -4
        return -8

    def _fit_delta(self, fit_label: str) -> int:
        return {
            "strong_fit": 8,
            "possible_fit": 3,
            "weak_fit": -2,
            "avoid": -12,
        }[fit_label]

    def _merge_stack(self, fallback_stack: list[str], parsed_stack: Iterable) -> list[str]:
        ordered: list[str] = []
        for item in [*fallback_stack, *list(parsed_stack or [])]:
            value = str(item).strip()
            if not value:
                continue
            label = self._canonical_stack_label(value)
            if label not in ordered:
                ordered.append(label)
        return ordered[:6]

    def _canonical_stack_label(self, value: str) -> str:
        normalized = value.strip().lower()
        return TECH_LABELS.get(
            normalized,
            normalized.upper() if len(normalized) <= 3 else normalized.title(),
        )

    def _clean_text(self, value, default: str, *, max_len: int) -> str:
        text = " ".join(str(value or "").split()).strip()
        if not text:
            text = default
        return text[:max_len].strip()

    def _select_reply(self, value, fallback: str) -> str:
        text = self._clean_text(value, fallback, max_len=420)
        lowered = text.lower()
        banned_openers = (
            "te invito",
            "te recomiendo",
            "puedes considerar",
            "deberias",
        )
        if lowered.startswith(banned_openers):
            return fallback
        return text

    def _merge_sector(self, value, fallback: str) -> str:
        parsed = self._clean_text(value, fallback, max_len=80)
        if parsed == "General Tech" and fallback != "General Tech":
            return fallback
        return parsed

    def _normalize_fit_label(self, value) -> str | None:
        value = str(value or "").strip().lower()
        return value if value in FIT_LABELS else None

    def _normalize_action(self, value) -> str | None:
        value = str(value or "").strip().lower()
        return value if value in ACTION_LABELS else None

    def _normalize_state(self, value) -> str | None:
        value = str(value or "").strip().upper()
        return value if value in PIPELINE_STATE_HINTS else None

    def _merge_score_delta(self, left: int, right: int) -> int:
        return max(-20, min(20, round((left + right) / 2)))

    def _clamp_int(self, value, *, minimum: int, maximum: int, default: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        return max(minimum, min(maximum, parsed))
