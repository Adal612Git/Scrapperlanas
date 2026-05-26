from __future__ import annotations

from typing import Any, Mapping

from .risk import assess_risk
from .schemas import OutreachDraftV2
from .utils import budget_amount, clean_text, coerce_list, technical_matches
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
    if quality.get("quality_stage") and quality.get("quality_stage") != "READY_TO_CONTACT":
        reasons = list(quality.get("rejection_reasons") or quality.get("risk_reasons") or [])
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

    pain_phrase = _join_human(pains[:3]) if pains else "mejorar una operacion tecnica con datos, automatizacion o integraciones"
    skill_phrase = _join_human(skills[:4]) if skills else "automatizacion, integraciones y visibilidad operativa"
    subject = f"Sobre {title[:70]}"
    cta = "revisamos el caso en una llamada breve de 20 minutos esta semana"

    if selected_tone == "directo":
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
