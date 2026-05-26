from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping


DRAFT_TYPES = (
    "initial",
    "consultative",
    "followup_1",
    "followup_2",
    "polite_close",
    "discovery_questions",
    "mini_proposal",
    "portal_response",
    "github_reply",
    "procurement_response",
)

CHANNELS = (
    "email",
    "portal",
    "linkedin_manual",
    "github_comment",
    "contact_form",
    "unknown",
)

FOLLOWUP_DRAFT_BY_COUNT = {
    0: "followup_1",
    1: "followup_2",
}


@dataclass(frozen=True)
class OutreachDraftData:
    draft_type: str
    channel: str
    subject: str
    body: str
    evidence_used: list[str]
    tone: str = "professional_direct"
    language: str = "es"
    quality_warnings: list[str] | None = None


def generate_outreach_drafts(opportunity: Mapping[str, Any]) -> list[OutreachDraftData]:
    opp = _normalize_opportunity(opportunity)
    channel = recommended_channel(opp)
    subject = suggested_subject(opp)
    initial = _initial_message(opp, channel)
    consultative = _consultative_message(opp)
    followup_1 = _followup_1(opp)
    followup_2 = _followup_2(opp)
    polite_close = _polite_close(opp)
    discovery_questions = _discovery_questions(opp)
    mini_proposal = _mini_proposal(opp)
    evidence = _evidence_used(opp)
    source_type = opp["source_type"]

    drafts = [
        _build_draft("initial", channel, subject, initial, evidence, opp),
        _build_draft("consultative", channel, subject, consultative, evidence, opp),
        _build_draft("followup_1", channel, f"Seguimiento: {subject}", followup_1, evidence, opp),
        _build_draft("followup_2", channel, f"Segundo seguimiento: {subject}", followup_2, evidence, opp),
        _build_draft("polite_close", channel, f"Cierre amable: {subject}", polite_close, evidence, opp),
        _build_draft("discovery_questions", channel, f"Discovery: {subject}", discovery_questions, evidence, opp),
        _build_draft("mini_proposal", channel, f"Mini propuesta: {subject}", mini_proposal, evidence, opp),
    ]

    if source_type == "procurement":
        drafts.append(
            _build_draft("procurement_response", "portal", subject, _procurement_response(opp), evidence, opp)
        )
    elif source_type == "github_issue":
        drafts.append(_build_draft("github_reply", "github_comment", subject, _github_reply(opp), evidence, opp))
    elif channel in {"portal", "contact_form"}:
        drafts.append(_build_draft("portal_response", channel, subject, _portal_response(opp), evidence, opp))

    return drafts


def validate_outreach_draft(draft: OutreachDraftData | Mapping[str, Any], opportunity: Mapping[str, Any]) -> list[str]:
    draft_type = str(_draft_value(draft, "draft_type") or "").strip()
    body = str(_draft_value(draft, "body") or "").strip()
    evidence_used = _coerce_list(_draft_value(draft, "evidence_used") or _draft_value(draft, "evidence_used_json"))
    opp = _normalize_opportunity(opportunity)
    warnings: list[str] = []

    word_count = _word_count(body)
    if draft_type == "initial" and word_count > 120:
        warnings.append("initial_message_over_120_words")
    if draft_type in {"followup_1", "followup_2", "polite_close"} and word_count > 90:
        warnings.append("followup_over_90_words")

    if (opp["pain_signals"] or opp["evidence_snippets"]) and not evidence_used:
        warnings.append("no_traceable_evidence")
    if not _mentions_any_signal(body, opp):
        warnings.append("does_not_mention_pain_or_evidence")

    if not opp["estimated_value"] and _mentions_budget(body):
        warnings.append("mentions_budget_without_source_value")
    if not opp["deadline_at"] and _mentions_deadline(body):
        warnings.append("mentions_deadline_without_source_deadline")
    if _has_aggressive_tone(body):
        warnings.append("aggressive_or_overclaiming_tone")
    if draft_type not in {"discovery_questions", "mini_proposal"} and not _has_clear_cta(body):
        warnings.append("missing_clear_cta")

    return warnings


def persist_outreach_drafts(db, *, opportunity_id: int, regenerate: bool = False) -> list[dict]:
    opportunity = db.execute("SELECT * FROM opportunities WHERE id = ?", (opportunity_id,)).fetchone()
    if opportunity is None:
        raise LookupError(f"Opportunity {opportunity_id} not found.")

    if not regenerate:
        existing = list_active_outreach_drafts(db, opportunity_id=opportunity_id)
        if existing:
            return existing

    if regenerate:
        db.execute(
            """
            UPDATE outreach_drafts
            SET is_active = 0,
                updated_at = CURRENT_TIMESTAMP
            WHERE opportunity_id = ? AND is_active = 1
            """,
            (opportunity_id,),
        )

    generated_at = datetime.now(UTC).replace(microsecond=0).isoformat() if regenerate else None
    drafts_to_store = _safe_outreach_drafts(dict(opportunity))
    for draft in drafts_to_store:
        db.execute(
            """
            UPDATE outreach_drafts
            SET is_active = 0,
                updated_at = CURRENT_TIMESTAMP
            WHERE opportunity_id = ?
              AND draft_type = ?
              AND channel = ?
              AND is_active = 1
            """,
            (opportunity_id, draft.draft_type, draft.channel),
        )
        db.execute(
            """
            INSERT INTO outreach_drafts (
                opportunity_id,
                draft_type,
                channel,
                subject,
                body,
                evidence_used_json,
                tone,
                language,
                quality_warnings_json,
                regenerated_at,
                is_active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                opportunity_id,
                draft.draft_type,
                draft.channel,
                draft.subject,
                draft.body,
                json.dumps(draft.evidence_used, ensure_ascii=False),
                draft.tone,
                draft.language,
                json.dumps(draft.quality_warnings or [], ensure_ascii=False),
                generated_at,
            ),
        )
    db.commit()
    return list_active_outreach_drafts(db, opportunity_id=opportunity_id)


def _safe_outreach_drafts(opportunity: Mapping[str, Any]) -> list[OutreachDraftData]:
    from .intelligence.outreach import outreach_blockers

    blockers = outreach_blockers(opportunity)
    if not blockers:
        return generate_outreach_drafts(opportunity)

    body = (
        "No recomiendo redactar outreach todavia.\n\n"
        "Falta validar:\n"
        + "\n".join(f"- {blocker}" for blocker in blockers[:6])
        + "\n\nChecklist de investigacion:\n"
        "- Buscar comprador real.\n"
        "- Validar dominio o portal oficial.\n"
        "- Confirmar presupuesto y deadline.\n"
        "- Revisar si es agregador, SEO, discusion o scam.\n"
        "- Guardar evidencia antes de contactar."
    )
    return [
        OutreachDraftData(
            draft_type="discovery_questions",
            channel="unknown",
            subject="No redactar todavia: falta validacion",
            body=body,
            evidence_used=[],
            tone="research_guardrail",
            language="es",
            quality_warnings=blockers,
        )
    ]


def list_active_outreach_drafts(db, *, opportunity_id: int) -> list[dict]:
    rows = db.execute(
        """
        SELECT *
        FROM outreach_drafts
        WHERE opportunity_id = ? AND is_active = 1
        ORDER BY
            CASE draft_type
                WHEN 'initial' THEN 1
                WHEN 'consultative' THEN 2
                WHEN 'followup_1' THEN 3
                WHEN 'followup_2' THEN 4
                WHEN 'polite_close' THEN 5
                WHEN 'mini_proposal' THEN 6
                WHEN 'discovery_questions' THEN 7
                ELSE 8
            END,
            id ASC
        """,
        (opportunity_id,),
    ).fetchall()
    return [serialize_outreach_draft(row) for row in rows]


def activate_outreach_draft(db, *, opportunity_id: int, draft_id: int) -> dict:
    draft = db.execute(
        "SELECT * FROM outreach_drafts WHERE id = ? AND opportunity_id = ?",
        (draft_id, opportunity_id),
    ).fetchone()
    if draft is None:
        raise LookupError(f"Outreach draft {draft_id} not found.")

    db.execute(
        """
        UPDATE outreach_drafts
        SET is_active = 0,
            updated_at = CURRENT_TIMESTAMP
        WHERE opportunity_id = ?
          AND draft_type = ?
          AND channel = ?
          AND id <> ?
        """,
        (opportunity_id, draft["draft_type"], draft["channel"], draft_id),
    )
    db.execute(
        """
        UPDATE outreach_drafts
        SET is_active = 1,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (draft_id,),
    )
    db.commit()
    updated = db.execute("SELECT * FROM outreach_drafts WHERE id = ?", (draft_id,)).fetchone()
    return serialize_outreach_draft(updated)


def serialize_outreach_draft(row) -> dict:
    draft = dict(row)
    draft["evidence_used"] = _coerce_list(draft.get("evidence_used_json"))
    draft["quality_warnings"] = _coerce_list(draft.get("quality_warnings_json"))
    return draft


def recommended_followup_type(followup_count: int | None) -> str:
    try:
        count = int(followup_count or 0)
    except (TypeError, ValueError):
        count = 0
    return FOLLOWUP_DRAFT_BY_COUNT.get(count, "polite_close")


def recommended_channel(opportunity: Mapping[str, Any]) -> str:
    opp = _normalize_opportunity(opportunity)
    contact_signals = " ".join(opp["contact_signals"]).lower()
    source_type = opp["source_type"]
    if "email:" in contact_signals or "@" in contact_signals:
        return "email"
    if source_type == "github_issue":
        return "github_comment"
    if source_type == "hiring_signal":
        return "linkedin_manual"
    if source_type in {"procurement", "grant", "direct_rfp"}:
        return "portal"
    if opp["apply_url"]:
        return "contact_form"
    return "unknown"


def suggested_subject(opportunity: Mapping[str, Any]) -> str:
    opp = _normalize_opportunity(opportunity)
    topic = _shorten(opp["title"] or opp["pain"] or "oportunidad tecnica", 72)
    if opp["source_type"] == "procurement":
        return f"Propuesta breve para {topic}"
    if opp["source_type"] == "github_issue":
        return f"Idea tecnica para {topic}"
    return f"Apoyo puntual para {topic}"


def _build_draft(
    draft_type: str,
    channel: str,
    subject: str,
    body: str,
    evidence: list[str],
    opportunity: Mapping[str, Any],
) -> OutreachDraftData:
    draft = OutreachDraftData(
        draft_type=draft_type,
        channel=channel if channel in CHANNELS else "unknown",
        subject=subject,
        body=_compact_body(body),
        evidence_used=evidence,
    )
    return OutreachDraftData(
        **{**draft.__dict__, "quality_warnings": validate_outreach_draft(draft, opportunity)}
    )


def _initial_message(opp: dict, channel: str) -> str:
    source_type = opp["source_type"]
    if source_type == "procurement":
        body = (
            f"{_greeting(opp, formal=True)}\n\n"
            f"Vi la oportunidad publicada sobre {opp['topic']}. Podemos apoyar con {opp['service']} "
            f"para entregar {opp['result']}. Por lo que se menciona en la publicacion, parece importante cubrir "
            f"{opp['pain']}. {_deadline_sentence(opp)}"
            "Si tiene sentido, puedo compartir una propuesta breve con alcance, tiempos y riesgos principales."
        )
    elif source_type == "hiring_signal":
        body = (
            f"{_greeting(opp)}\n\n"
            f"Note que estan fortaleciendo capacidades en {opp['area']}. Si necesitan avanzar en {opp['result']} "
            f"antes de cerrar contrataciones o liberar carga del equipo actual, puedo apoyar de forma puntual con "
            f"{opp['service']}. {_deadline_sentence(opp)}"
            "Puedo compartir una propuesta breve con entregables y tiempos."
        )
    elif source_type == "github_issue":
        body = (
            f"{_greeting(opp)}\n\n"
            f"Vi el issue relacionado con {opp['pain']}. Puedo proponer una solucion pequena primero para validar "
            "el enfoque, dejar un plan claro y reducir riesgo antes de tocar mas partes del proyecto. "
            "Si sirve, comparto una propuesta tecnica corta."
        )
    elif source_type == "grant":
        body = (
            f"{_greeting(opp, formal=True)}\n\n"
            f"Vi que el proyecto esta relacionado con {opp['topic']}. Puedo apoyar la parte tecnica de implementacion, "
            "automatizacion, dashboards, reporting o integracion de datos para avanzar con entregables claros. "
            f"Si tiene sentido, preparo una propuesta breve enfocada en {opp['pain']}."
        )
    else:
        body = (
            f"{_greeting(opp)}\n\n"
            f"Vi la publicacion sobre {opp['topic']}. Podemos apoyar con {opp['service']} para entregar "
            f"{opp['result']}. Lo mas relevante parece ser {opp['pain']}. {_deadline_sentence(opp)}"
            "Si tiene sentido, comparto una propuesta corta con alcance y siguiente paso."
        )
    return body


def _consultative_message(opp: dict) -> str:
    return (
        f"{_greeting(opp)}\n\n"
        f"Para aterrizar {opp['topic']}, revisaria primero tres puntos: resultado esperado, sistemas o datos involucrados "
        f"y restricciones de tiempo. Con eso puedo proponer una primera entrega pequena enfocada en {opp['pain']} "
        "y dejar claro que conviene construir ahora, que se puede posponer y que riesgos hay que controlar. "
        "Tiene sentido que lo aterrice en una propuesta breve?"
    )


def _followup_1(opp: dict) -> str:
    return (
        f"{_greeting(opp)}\n\n"
        f"Retomo mi mensaje sobre {opp['topic']}. Puedo ayudar a convertir {opp['pain']} en un primer entregable claro, "
        "con alcance acotado y sin compromiso largo. Si conviene, le comparto una propuesta breve para revisarla."
    )


def _followup_2(opp: dict) -> str:
    return (
        f"{_greeting(opp)}\n\n"
        f"Hago un segundo seguimiento. Si {opp['topic']} sigue activo, puedo preparar una ruta corta para avanzar "
        f"en {opp['result']} y validar rapido si hay fit. Si no es prioridad ahora, lo dejo en pausa sin problema."
    )


def _polite_close(opp: dict) -> str:
    return (
        f"{_greeting(opp)}\n\n"
        f"Para no insistir de mas, cierro el seguimiento sobre {opp['topic']}. Si despues necesitan apoyo con "
        f"{opp['service']}, puedo retomar con una propuesta concreta y alcance pequeno."
    )


def _discovery_questions(opp: dict) -> str:
    return "\n".join(
        [
            f"1. Que resultado concreto debe quedar listo para considerar exitoso {opp['topic']}?",
            "2. Que sistemas, datos o APIs ya existen y cuales habria que integrar?",
            "3. Hay una fecha limite real o una ventana interna para decidir?",
            "4. Que restriccion pesa mas: tiempo, recursos disponibles, seguridad, aprobacion o calidad de datos?",
            "5. Quien revisa el entregable tecnico y quien aprueba el siguiente paso?",
        ]
    )


def _mini_proposal(opp: dict) -> str:
    return "\n".join(
        [
            f"- Objetivo: avanzar en {opp['result']} con alcance acotado.",
            f"- Primer entregable: diagnostico rapido y plan tecnico para {opp['pain']}.",
            f"- Ejecucion: construir o ajustar {opp['service']} con evidencia revisable.",
            "- Control: reporte corto de avances, riesgos y decisiones pendientes.",
            "- Cierre: demo o entrega documentada para decidir si conviene ampliar alcance.",
        ]
    )


def _procurement_response(opp: dict) -> str:
    return (
        f"{_greeting(opp, formal=True)}\n\n"
        f"Con relacion a la oportunidad publicada sobre {opp['topic']}, podemos apoyar con {opp['service']} "
        f"para entregar {opp['result']}. La publicacion resalta {opp['pain']}, por lo que propondria un alcance "
        "inicial con entregables, cronograma, supuestos y riesgos. Quedo atento para compartir una propuesta breve "
        "o responder el formato requerido por el portal."
    )


def _github_reply(opp: dict) -> str:
    return (
        f"{_greeting(opp)}\n\n"
        f"Vi el issue sobre {opp['pain']}. Puedo revisar el contexto y proponer una solucion pequena primero, "
        "idealmente con cambios faciles de validar y una explicacion tecnica clara. Si les sirve, comparto un plan "
        "corto antes de abrir PR o tocar el codigo."
    )


def _portal_response(opp: dict) -> str:
    return (
        f"{_greeting(opp, formal=True)}\n\n"
        f"Quisiera participar en la oportunidad sobre {opp['topic']}. Podemos apoyar con {opp['service']} y "
        f"enfocar la primera entrega en {opp['pain']}. Puedo enviar alcance, tiempos y preguntas de validacion "
        "por este canal si todavia estan recibiendo propuestas."
    )


def _normalize_opportunity(opportunity: Mapping[str, Any]) -> dict:
    opp = dict(opportunity)
    required_skills = _coerce_list(opp.get("required_skills") or opp.get("stack"))
    pain_signals = _coerce_list(opp.get("pain_signals"))
    evidence_snippets = _coerce_list(opp.get("evidence_snippets"))
    contact_signals = _coerce_list(opp.get("contact_signals"))
    title = _clean_text(opp.get("title")) or "la oportunidad"
    pain = _first_clean(pain_signals) or _first_clean(evidence_snippets) or title
    service = _service_phrase(required_skills, str(opp.get("source_type") or ""))
    result = _result_phrase(opp, service)
    topic = _shorten(title, 86)
    area = _area_phrase(required_skills, topic)
    source_type = str(opp.get("source_type") or "generic").strip().lower() or "generic"
    return {
        **opp,
        "source_type": source_type,
        "title": title,
        "topic": topic,
        "area": area,
        "pain": _shorten(pain, 150),
        "service": service,
        "result": result,
        "required_skills": required_skills,
        "pain_signals": pain_signals,
        "evidence_snippets": evidence_snippets,
        "contact_signals": contact_signals,
        "buyer_name": _clean_text(opp.get("buyer_name") or opp.get("company")),
        "deadline_at": _clean_text(opp.get("deadline_at")),
        "estimated_value": opp.get("estimated_value"),
        "currency": _clean_text(opp.get("currency")) or "USD",
        "apply_url": _clean_text(opp.get("apply_url") or opp.get("url")),
    }


def _service_phrase(required_skills: list[str], source_type: str) -> str:
    skills = [_humanize_skill(skill) for skill in required_skills[:3] if _humanize_skill(skill)]
    if skills:
        return ", ".join(skills)
    if source_type == "grant":
        return "implementacion tecnica, reporting e integracion de datos"
    if source_type == "github_issue":
        return "diagnostico tecnico y solucion acotada"
    return "automatizacion, integraciones API y dashboards"


def _result_phrase(opp: Mapping[str, Any], service: str) -> str:
    source_type = str(opp.get("source_type") or "").lower()
    if source_type == "procurement":
        return "un entregable tecnico documentado y facil de evaluar"
    if source_type == "hiring_signal":
        return "avance operativo antes de depender de una contratacion completa"
    if source_type == "github_issue":
        return "una mejora pequena, revisable y con bajo riesgo"
    if source_type == "grant":
        return "entregables tecnicos medibles para el proyecto"
    if "dashboard" in service:
        return "visibilidad operativa y decisiones mas rapidas"
    return "un primer resultado funcional y medible"


def _area_phrase(required_skills: list[str], fallback: str) -> str:
    if required_skills:
        return ", ".join(_humanize_skill(skill) for skill in required_skills[:2])
    return fallback


def _evidence_used(opp: Mapping[str, Any]) -> list[str]:
    values = []
    for value in [*opp["pain_signals"], *opp["evidence_snippets"], *opp["required_skills"]]:
        cleaned = _shorten(_clean_text(value), 180)
        if cleaned and cleaned not in values:
            values.append(cleaned)
    return values[:4]


def _greeting(opp: Mapping[str, Any], *, formal: bool = False) -> str:
    buyer_name = _clean_text(opp.get("buyer_name"))
    if formal:
        return "Buen dia,"
    if buyer_name:
        return f"Hola equipo de {buyer_name},"
    return "Hola,"


def _deadline_sentence(opp: Mapping[str, Any]) -> str:
    if not opp.get("deadline_at"):
        return ""
    return "Si la fecha limite esta cerca, conviene acotar alcance y riesgos desde el primer intercambio. "


def _mentions_any_signal(body: str, opp: Mapping[str, Any]) -> bool:
    body_key = _normalize_for_match(body)
    candidates = [opp.get("pain"), opp.get("topic"), *opp.get("pain_signals", ()), *opp.get("evidence_snippets", ())]
    for candidate in candidates:
        words = [word for word in _normalize_for_match(candidate).split() if len(word) >= 5]
        if any(word in body_key for word in words[:4]):
            return True
    return False


def _mentions_budget(body: str) -> bool:
    text = _normalize_for_match(body)
    return any(token in text for token in ("presupuesto", "budget", "valor estimado", " usd ", "$"))


def _mentions_deadline(body: str) -> bool:
    text = _normalize_for_match(body)
    return any(token in text for token in ("deadline", "fecha limite", "fecha lmite", "urgencia", "vence"))


def _has_aggressive_tone(body: str) -> bool:
    text = _normalize_for_match(body)
    red_flags = (
        "desesperado",
        "desesperados",
        "garantizo",
        "aseguro ventas",
        "necesitan ya",
        "deben contratar",
        "mi bot",
        "scrapee",
        "scraper encontro",
    )
    return any(flag in text for flag in red_flags)


def _has_clear_cta(body: str) -> bool:
    text = _normalize_for_match(body)
    return "?" in body or any(
        phrase in text
        for phrase in (
            "tiene sentido",
            "comparto",
            "compartir",
            "puedo enviar",
            "quedo atento",
            "si sirve",
            "si les sirve",
            "si conviene",
        )
    )


def _draft_value(draft: OutreachDraftData | Mapping[str, Any], key: str):
    if isinstance(draft, OutreachDraftData):
        return getattr(draft, key, None)
    return draft.get(key)


def _coerce_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [_clean_text(item) for item in value if _clean_text(item)]
    if isinstance(value, tuple):
        return [_clean_text(item) for item in value if _clean_text(item)]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return [text]
        return _coerce_list(parsed)
    return [_clean_text(value)] if _clean_text(value) else []


def _clean_text(value) -> str:
    return " ".join(str(value or "").replace("\r", " ").split()).strip()


def _compact_body(value: str) -> str:
    lines = [" ".join(line.split()).strip() for line in str(value or "").strip().splitlines()]
    compacted = "\n".join(line for line in lines if line)
    return compacted.strip()


def _first_clean(values: list[str]) -> str:
    for value in values:
        cleaned = _clean_text(value)
        if cleaned:
            return cleaned
    return ""


def _shorten(value: str, limit: int) -> str:
    text = _clean_text(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip(" .,;:") + "..."


def _humanize_skill(value: str) -> str:
    cleaned = _clean_text(value).replace("_", " ").replace("-", " ")
    return cleaned[:1].lower() + cleaned[1:] if cleaned else ""


def _normalize_for_match(value) -> str:
    text = _clean_text(value).lower()
    return f" {text} "


def _word_count(value: str) -> int:
    return len(re.findall(r"\b[\w@.-]+\b", value or ""))
