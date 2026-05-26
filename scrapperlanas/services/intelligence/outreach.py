from __future__ import annotations

from typing import Any, Mapping

from .risk import assess_risk
from .schemas import CopilotModeResult, OutreachDraftV2
from .utils import budget_amount, clean_text, coerce_list, technical_matches
from ..lead_explainer import explain_opportunity
from ..quality import assess_opportunity_quality


TONE_LABELS = {
    "consultivo": "consultivo",
    "directo": "directo",
    "tecnico": "tecnico",
    "procurement": "procurement",
    "github": "GitHub tecnico",
    "freelance": "freelance breve",
    "seguimiento": "seguimiento",
    "cierre": "cierre educado",
    "propuesta": "propuesta corta",
}


def compose_outreach(opportunity: Mapping[str, Any], *, tone: str = "consultivo", language: str = "es") -> OutreachDraftV2:
    selected_tone = TONE_LABELS.get(clean_text(tone).lower(), "consultivo")
    quality = _quality_payload(opportunity)
    blockers = outreach_blockers(opportunity)
    if blockers:
        reasons = blockers or list(quality.get("rejection_reasons") or quality.get("risk_reasons") or [])
        reason_lines = "\n".join(f"{index}. {reason}" for index, reason in enumerate(reasons[:4], start=1))
        body = (
            "No recomiendo contactar esta oportunidad todavia.\n\n"
            "Motivos:\n"
            f"{reason_lines or '1. Falta evidencia comercial verificable.'}\n\n"
            "Siguiente paso: buscar comprador, dominio, deadline, presupuesto valido y canal oficial antes de redactar."
        )
        return OutreachDraftV2(
            tone=selected_tone,
            subject="No contactar todavia",
            body=body,
            personalization_bullets=[],
            risks=list(quality.get("risk_reasons") or []),
            suggested_cta="Validar evidencia comercial antes de contactar",
            language=language,
            confidence=90,
        )

    buyer = clean_text(opportunity.get("buyer_name") or opportunity.get("company")) or "equipo"
    title = clean_text(opportunity.get("title")) or "su iniciativa"
    pains = coerce_list(opportunity.get("pain_signals"))
    evidence = coerce_list(opportunity.get("evidence") or opportunity.get("evidence_snippets"))
    skills = technical_matches(opportunity)
    amount = budget_amount(opportunity)
    risk = assess_risk(opportunity)
    intent = clean_text(quality.get("commercial_intent_label")).upper()

    pain_phrase = _join_human(pains[:3]) if pains else "mejorar una operacion tecnica con datos, automatizacion o integraciones"
    skill_phrase = _join_human(skills[:4]) if skills else "automatizacion, integraciones y visibilidad operativa"
    subject = f"Sobre {title[:70]}"
    cta = "revisamos el caso en una llamada breve de 20 minutos esta semana"

    if intent in {"PROCUREMENT", "RFP"}:
        subject = f"Consulta sobre {title[:62]}"
        body = (
            f"Buen dia {buyer},\n\n"
            f"Revisamos la oportunidad {title}. Por la evidencia disponible, el alcance parece relacionado con {skill_phrase} y con una necesidad concreta: {pain_phrase}.\n\n"
            "Podemos preparar una respuesta formal con alcance, entregables, supuestos, riesgos y tiempos, siempre siguiendo el canal y lineamientos del proceso.\n\n"
            "Nos indican el canal correcto o los requisitos para presentar propuesta?"
        )
        cta = "validar canal formal y lineamientos de propuesta"
    elif intent == "BUYER_PAIN":
        subject = f"Pregunta sobre {title[:66]}"
        body = (
            f"Hola {buyer},\n\n"
            f"Vi que estan explorando {pain_phrase}. Antes de proponer algo, quisiera entender si ya estan buscando implementar una solucion o si todavia estan comparando opciones.\n\n"
            f"Si el problema sigue abierto, puedo compartir una ruta breve para ordenar {skill_phrase} sin inflar el alcance.\n\n"
            "Tiene sentido revisarlo en una llamada corta?"
        )
        cta = "validar si hay compra real antes de proponer"
    elif selected_tone == "directo":
        body = (
            f"Hola {buyer},\n\n"
            f"Vi {title} y parece haber una necesidad concreta alrededor de {pain_phrase}.\n\n"
            f"Podemos ayudar con {skill_phrase}, manteniendo el alcance claro y medible desde el inicio.\n\n"
            f"Te parece si {cta}?"
        )
    elif selected_tone == "procurement":
        body = (
            f"Hola {buyer},\n\n"
            f"Revisamos la oportunidad {title}. El caso parece alineado con {skill_phrase} y con una necesidad documentada: {pain_phrase}.\n\n"
            "Podemos preparar una respuesta estructurada con alcance, entregables, supuestos, riesgos y tiempos.\n\n"
            "Nos comparten el canal correcto para presentar una propuesta formal?"
        )
    elif selected_tone == "github":
        body = (
            f"Hola {buyer},\n\n"
            f"Vi el issue sobre {title}. Por lo que describen, el punto clave parece ser {pain_phrase}.\n\n"
            f"Puedo proponer una ruta tecnica concreta usando {skill_phrase}, con cambios pequenos, verificables y sin prometer mas de lo que el repo permita validar.\n\n"
            "Les sirve si comparto un plan corto de implementacion en el issue?"
        )
    elif selected_tone == "seguimiento":
        body = (
            f"Hola {buyer},\n\n"
            f"Solo doy seguimiento al mensaje sobre {title}. El caso sigue pareciendo relevante por {pain_phrase}.\n\n"
            "Si todavia esta abierto, puedo mandar una propuesta corta con alcance y siguiente paso.\n\n"
            "Lo revisamos esta semana?"
        )
    elif selected_tone == "cierre":
        body = (
            f"Hola {buyer},\n\n"
            f"Cierro el hilo sobre {title} para no insistir de mas.\n\n"
            f"Si vuelve a ser prioridad resolver {pain_phrase}, con gusto retomamos con una propuesta breve y accionable.\n\n"
            "Saludos,\nLoto Signal"
        )
    elif selected_tone == "propuesta":
        body = (
            f"Hola {buyer},\n\n"
            f"Para {title}, proponemos un primer alcance enfocado en {pain_phrase}.\n\n"
            f"Entregables sugeridos: diagnostico breve, flujo tecnico, automatizacion/integracion principal y tablero de seguimiento. Stack probable: {skill_phrase}.\n\n"
            "Si hace sentido, armamos estimacion formal con tiempos y supuestos."
        )
    else:
        body = (
            f"Hola {buyer},\n\n"
            f"Vi que estan buscando resolver {pain_phrase}.\n\n"
            f"Podemos ayudarles a convertir ese flujo en una operacion mas clara: {skill_phrase}, metricas accionables y seguimiento tecnico.\n\n"
            f"Les parece si {cta}?\n\n"
            "Saludos,\nLoto Signal"
        )

    bullets = []
    if pains:
        bullets.append(f"Dolor usado: {pains[0]}")
    if skills:
        bullets.append(f"Skills usados: {', '.join(skills[:4])}")
    if amount:
        bullets.append(f"Valor visible usado: {amount:,}")
    if evidence:
        bullets.append(f"Evidencia usada: {evidence[0]}")

    confidence = 88 if (pains or evidence) and skills else 72
    if risk.scam_risk >= 50:
        confidence -= 20

    return OutreachDraftV2(
        tone=selected_tone,
        subject=subject,
        body=body,
        personalization_bullets=bullets,
        risks=risk.warnings,
        suggested_cta=cta,
        language=language,
        confidence=max(35, min(95, confidence)),
    )


def evaluate_opportunity(opportunity: Mapping[str, Any]) -> CopilotModeResult:
    explanation = explain_opportunity(opportunity)
    return CopilotModeResult(
        mode="evaluar",
        verdict=explanation.verdict,
        reasons=[*explanation.positive_reasons, *explanation.negative_reasons],
        missing_evidence=explanation.missing_evidence,
        blocking_conditions=outreach_blockers(opportunity),
        draft=None,
        checklist=[],
    )


def research_checklist(opportunity: Mapping[str, Any]) -> CopilotModeResult:
    explanation = explain_opportunity(opportunity)
    missing = set(explanation.missing_evidence)
    checklist = [
        "Buscar comprador real y responsable de decision.",
        "Validar dominio o portal oficial.",
        "Confirmar si hay presupuesto comercial valido.",
        "Detectar deadline y canal de aplicacion.",
        "Diferenciar post original de agregador o contenido SEO.",
        "Revisar senales de scam antes de contactar.",
    ]
    if "comprador, empresa o dominio oficial" in missing:
        checklist.insert(0, "No asumir que usuario/subreddit es comprador.")
    return CopilotModeResult(
        mode="investigar",
        verdict=explanation.verdict,
        reasons=explanation.negative_reasons,
        missing_evidence=explanation.missing_evidence,
        blocking_conditions=outreach_blockers(opportunity),
        draft=None,
        checklist=list(dict.fromkeys(checklist)),
    )


def copilot_mode(opportunity: Mapping[str, Any], *, mode: str = "evaluar", tone: str = "consultivo") -> CopilotModeResult:
    selected = clean_text(mode).lower()
    if selected == "investigar":
        return research_checklist(opportunity)
    if selected == "redactar":
        draft = compose_outreach(opportunity, tone=tone)
        blockers = outreach_blockers(opportunity)
        return CopilotModeResult(
            mode="redactar",
            verdict="BLOCKED" if blockers else "READY",
            reasons=[] if not blockers else blockers,
            missing_evidence=[],
            blocking_conditions=blockers,
            draft=draft.to_dict(),
            checklist=[],
        )
    return evaluate_opportunity(opportunity)


def outreach_blockers(opportunity: Mapping[str, Any]) -> list[str]:
    quality = _quality_payload(opportunity)
    stage = clean_text(quality.get("quality_stage")).upper()
    risk_level = clean_text(quality.get("risk_level")).upper()
    intent = clean_text(quality.get("commercial_intent_label")).upper()
    buyer_confidence = _safe_int(quality.get("buyer_confidence"))
    state = clean_text(opportunity.get("state")).upper()
    rejection_reasons = [clean_text(reason) for reason in quality.get("rejection_reasons") or []]
    has_good_override = "human_feedback_good_lead" in rejection_reasons
    blockers: list[str] = []

    if state in {"DESCARTADO", "SOSPECHOSO"} and not has_good_override:
        blockers.append(f"Estado {state.lower()} bloquea outreach directo.")
    if stage and stage != "READY_TO_CONTACT" and not has_good_override:
        blockers.append(f"Quality stage {stage}: falta evidencia comercial.")
    if risk_level in {"HIGH", "CRITICAL"}:
        blockers.append(f"Riesgo {risk_level.lower()} antes de contactar.")
    if buyer_confidence < 50 and not has_good_override:
        blockers.append("Comprador verificable insuficiente.")
    if intent in {"FICTION", "CONSPIRACY", "NEWS", "DISCUSSION", "SEO_CONTENT", "SELF_PROMO", "MARKET_RESEARCH"}:
        blockers.append(f"Intencion {intent}: no es solicitud comercial directa.")
    if not blockers and not (opportunity.get("apply_url") or coerce_list(opportunity.get("contact_signals"))):
        blockers.append("Falta canal oficial o contacto accionable.")
    return list(dict.fromkeys(blockers))


def _join_human(values: list[str]) -> str:
    cleaned = [clean_text(value) for value in values if clean_text(value)]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]
    return ", ".join(cleaned[:-1]) + " y " + cleaned[-1]


def _quality_payload(opportunity: Mapping[str, Any]) -> dict:
    raw_quality = opportunity.get("quality")
    if isinstance(raw_quality, dict):
        return raw_quality
    analysis = opportunity.get("analysis")
    if isinstance(analysis, dict) and isinstance(analysis.get("quality"), dict):
        return analysis["quality"]
    return assess_opportunity_quality(opportunity).to_dict()


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
