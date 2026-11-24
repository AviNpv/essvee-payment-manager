"""Meta WhatsApp Business Cloud API integration.

All outbound business messages use approved templates. No unofficial WhatsApp
automation or browser scraping is used anywhere in this application.
"""

import json
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import requests

from app.core.config import settings as env
from app.core.errors import ConfigurationError, NotFoundError, ProviderError, ValidationError
from app.models import (
    ConsentStatus,
    Customer,
    CustomerStatus,
    MessageDirection,
    MessageStatus,
    MessageType,
    WhatsAppMessage,
)
from app.services import audit_service, customer_service, settings_service

GRAPH_BASE = "https://graph.facebook.com"
TIMEOUT = (5, 20)
OPT_OUT_WORDS = {"stop", "stopall", "unsubscribe", "cancel", "end", "quit", "no", "optout"}


def credentials(db) -> dict[str, str]:
    return settings_service.whatsapp_credentials(db)


def is_configured(db) -> bool:
    creds = credentials(db)
    return bool(creds["access_token"] and creds["phone_number_id"])


def _headers(db) -> dict[str, str]:
    creds = credentials(db)
    if not creds["access_token"] or not creds["phone_number_id"]:
        raise ConfigurationError(
            "WhatsApp Cloud API is not configured. Add the access token and Phone Number ID in Settings → WhatsApp."
        )
    return {"Authorization": f"Bearer {creds['access_token']}", "Content-Type": "application/json"}


def money(amount: Decimal) -> str:
    return f"{Decimal(str(amount)):,.2f}"


def template_definitions() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[1] / "templates" / "whatsapp_templates.json"
    return json.loads(path.read_text(encoding="utf-8"))


def render_preview(
    db, message_type: str, name: str, amount: Decimal, remaining: Decimal | None = None, url: str = ""
) -> str:
    company = settings_service.company_name(db)
    if message_type == MessageType.CONFIRMATION.value:
        return (
            f"Hello {name},\n\n{company} has confirmed your payment of ₹{money(amount)}.\n\n"
            f"Your remaining outstanding balance is ₹{money(remaining or Decimal('0'))}.\n\n"
            f"Thank you for your payment!"
        )
    if message_type == MessageType.PARTIAL.value:
        return (
            f"Hello {name},\n\nWe have updated your payment records.\n\n"
            f"Your remaining outstanding balance is ₹{money(amount)}.\n\n"
            f"Kindly complete the remaining payment using the button below.\n\nThank you,\n{company}."
        )
    return (
        f"Hello {name},\n\nGreetings from {company}.\n\n"
        f"This is a friendly reminder that your outstanding payment is ₹{money(amount)}.\n\n"
        f"Kindly complete your payment using the Pay Now button below.\n\n"
        f"Thank you for choosing {company}."
    )


def _template_payload(
    db,
    *,
    template_name: str,
    to: str,
    body_parameters: list[str],
    button_url: str | None = None,
) -> dict[str, Any]:
    components: list[dict[str, Any]] = [
        {
            "type": "body",
            "parameters": [{"type": "text", "text": str(p)} for p in body_parameters],
        }
    ]
    if button_url:
        components.append(
            {
                "type": "button",
                "sub_type": "url",
                "index": "0",
                "parameters": [{"type": "text", "text": button_url}],
            }
        )
    creds = credentials(db)
    return {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": "en"},
            "components": components,
        },
        "_meta": {"waba_id": creds["waba_id"], "api_version": creds["api_version"]},
    }


def _post_message(db, payload: dict[str, Any]) -> dict[str, Any]:
    creds = credentials(db)
    headers = _headers(db)
    url = (
        f"{GRAPH_BASE}/{creds['api_version']}/{creds['phone_number_id']}/messages"
    )
    outgoing = {k: v for k, v in payload.items() if k != "_meta"}
    last_error = "unknown error"
    for attempt in range(3):
        try:
            response = requests.post(url, headers=headers, json=outgoing, timeout=TIMEOUT)
        except requests.RequestException as exc:
            last_error = f"{type(exc).__name__}: network error"
            if attempt < 2:
                time.sleep(2**attempt)
                continue
            raise ProviderError(f"WhatsApp API request failed: {last_error}") from exc
        if response.status_code < 400:
            try:
                return response.json()
            except ValueError as exc:
                raise ProviderError("WhatsApp API returned an invalid JSON response") from exc
        try:
            error = response.json().get("error", {})
            last_error = f"HTTP {response.status_code}: {error.get('message', 'unknown error')}"[:400]
        except ValueError:
            last_error = f"HTTP {response.status_code}"
        code = response.status_code
        if code == 429 or 500 <= code < 600:
            if attempt < 2:
                time.sleep(2**attempt)
                continue
        raise ProviderError(f"WhatsApp API error: {last_error}")
    raise ProviderError(f"WhatsApp API error: {last_error}")


def existing_message_ids(db, customer_id: UUID) -> set[UUID]:
    """IDs already recorded for a customer, used to detect duplicate sends."""
    return {
        row[0]
        for row in db.query(WhatsAppMessage.id)
        .filter(WhatsAppMessage.customer_id == customer_id)
        .all()
    }


def _daily_usage(db) -> int:
    ist = timezone(timedelta(hours=5, minutes=30))
    start = datetime.now(ist).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    return (
        db.query(WhatsAppMessage)
        .filter(
            WhatsAppMessage.direction == MessageDirection.OUTBOUND.value,
            WhatsAppMessage.created_at >= start,
            WhatsAppMessage.status != MessageStatus.FAILED.value,
        )
        .count()
    )


def check_rate_limit(db) -> None:
    from app.services.settings_service import get_reminder

    limit = get_reminder(db).daily_limit
    if _daily_usage(db) >= limit:
        raise ValidationError(f"Daily WhatsApp sending limit ({limit}) reached")


def send_template(
    db,
    customer: Customer,
    *,
    message_type: str,
    template_name: str,
    body_parameters: list[str],
    idempotency_key: str,
    button_url: str | None = None,
    check_eligibility: bool = True,
    skip_rate_limit: bool = False,
) -> WhatsAppMessage:
    """Send one templated message. Idempotent on idempotency_key."""
    existing = (
        db.query(WhatsAppMessage)
        .filter(WhatsAppMessage.idempotency_key == idempotency_key)
        .first()
    )
    if existing:
        return existing
    if check_eligibility:
        if customer.consent_status != ConsentStatus.OPT_IN.value:
            raise ValidationError(
                "Customer has not opted in to WhatsApp messages (or has opted out)"
            )
        if customer.status != CustomerStatus.ACTIVE.value:
            raise ValidationError("Customer is not active")
    if not customer_service.is_valid_phone(customer.whatsapp_number):
        raise ValidationError("Invalid WhatsApp number; message not sent")
    if not skip_rate_limit:
        check_rate_limit(db)

    payload = _template_payload(
        db,
        template_name=template_name,
        to=customer.whatsapp_number,
        body_parameters=body_parameters,
        button_url=button_url,
    )
    def _parse(param: str) -> Decimal:
        return Decimal(str(param).replace(",", ""))

    body_preview = render_preview(
        db,
        message_type,
        customer.name,
        _parse(body_parameters[1]) if len(body_parameters) > 1 else Decimal("0"),
        _parse(body_parameters[2]) if len(body_parameters) > 2 else None,
        button_url or "",
    )
    message = WhatsAppMessage(
        customer_id=customer.id,
        provider_message_id=None,
        direction=MessageDirection.OUTBOUND.value,
        message_type=message_type,
        template_name=template_name,
        status=MessageStatus.QUEUED.value,
        idempotency_key=idempotency_key,
        phone_number=customer.whatsapp_number,
        body_preview=body_preview,
        button_url=button_url,
        request_payload={k: v for k, v in payload.items() if k != "_meta"},
    )
    db.add(message)
    db.commit()  # reserve the idempotency key before calling the provider
    db.refresh(message)

    try:
        result = _post_message(db, payload)
        provider_id = None
        for item in result.get("messages", []) or []:
            provider_id = item.get("id")
            break
        message.provider_message_id = provider_id
        message.status = MessageStatus.SENT.value
        message.sent_at = datetime.now(timezone.utc)
        message.error = None
    except (ProviderError, ConfigurationError) as exc:
        message.status = MessageStatus.FAILED.value
        message.error = str(exc)[:500]
        message.retry_count = 1
    db.commit()
    db.refresh(message)
    audit_service.log(
        db,
        action=f"whatsapp.send.{message_type}",
        entity_type="WHATSAPP_MESSAGE",
        entity_id=str(message.id),
        after={"status": message.status, "template": template_name, "customer_id": str(customer.id)},
        commit=True,
    )
    return message


def send_reminder(
    db,
    customer: Customer,
    link_url: str,
    *,
    partial: bool = False,
    force: bool = False,
    idempotency_suffix: str | None = None,
) -> WhatsAppMessage:
    from app.services.settings_service import get_reminder

    config = get_reminder(db)
    key = (
        f"reminder:{customer.id}:{idempotency_suffix or link_url}"
        if not partial
        else f"partial:{customer.id}:{idempotency_suffix or link_url}"
    )
    if not force and (
        db.query(WhatsAppMessage).filter(WhatsAppMessage.idempotency_key == key).first()
    ):
        return (
            db.query(WhatsAppMessage).filter(WhatsAppMessage.idempotency_key == key).first()
        )
    if customer.outstanding_balance <= 0:
        raise ValidationError("Cannot send a reminder to a customer with zero outstanding balance")
    template = config.partial_template if partial else config.reminder_template
    message_type = MessageType.PARTIAL.value if partial else MessageType.REMINDER.value
    return send_template(
        db,
        customer,
        message_type=message_type,
        template_name=template,
        body_parameters=[customer.name, money(customer.outstanding_balance)],
        idempotency_key=key,
        button_url=link_url,
        check_eligibility=not force,
    )


def send_confirmation(
    db, customer: Customer, amount: Decimal, remaining: Decimal, payment_id: UUID
) -> WhatsAppMessage:
    config = settings_service.get_reminder(db)
    return send_template(
        db,
        customer,
        message_type=MessageType.CONFIRMATION.value,
        template_name=config.confirmation_template,
        body_parameters=[customer.name, money(amount), money(remaining)],
        idempotency_key=f"confirmation:{payment_id}",
        check_eligibility=False,
    )


def send_test(db, phone_number: str | None = None) -> dict[str, Any]:
    """Connection test: fetch phone number details from Graph API (no message sent)."""
    if not is_configured(db):
        raise ConfigurationError("WhatsApp credentials are not configured")
    creds = credentials(db)
    url = f"{GRAPH_BASE}/{creds['api_version']}/{creds['phone_number_id']}"
    try:
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {creds['access_token']}"},
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        raise ProviderError(f"WhatsApp connection failed: {type(exc).__name__}") from exc
    if response.status_code >= 400:
        raise ProviderError(f"WhatsApp API returned HTTP {response.status_code}")
    try:
        data = response.json()
    except ValueError as exc:
        raise ProviderError("WhatsApp API returned invalid JSON") from exc
    return {
        "status": "ok",
        "phone_number_id": creds["phone_number_id"],
        "display_phone_number": data.get("verified_name") or data.get("display_phone_number", ""),
    }


def process_statuses(db, value: dict[str, Any]) -> int:
    changed = 0
    for entry in value.get("statuses", []) or []:
        provider_id = entry.get("id")
        if not provider_id:
            continue
        message = (
            db.query(WhatsAppMessage)
            .filter(WhatsAppMessage.provider_message_id == provider_id)
            .first()
        )
        if not message:
            continue
        status = str(entry.get("status", "")).lower()
        mapped = {
            "sent": MessageStatus.SENT.value,
            "delivered": MessageStatus.DELIVERED.value,
            "read": MessageStatus.READ.value,
            "failed": MessageStatus.FAILED.value,
        }.get(status)
        if mapped:
            message.status = mapped
            if mapped == MessageStatus.DELIVERED.value:
                message.delivered_at = datetime.now(timezone.utc)
            if mapped == MessageStatus.FAILED.value:
                errors = entry.get("errors") or []
                message.error = str(errors[0].get("title", "Delivery failed"))[:500] if errors else "Delivery failed"
            changed += 1
    if changed:
        db.commit()
    return changed


def process_inbound(db, value: dict[str, Any]) -> int:
    changed = 0
    for message in value.get("messages", []) or []:
        provider_id = message.get("id")
        sender = message.get("from")
        if not sender:
            continue
        text = ""
        if message.get("type") == "text":
            text = (message.get("text") or {}).get("body", "")
        also = (
            db.query(WhatsAppMessage)
            .filter(WhatsAppMessage.idempotency_key == f"inbound:{provider_id}")
            .first()
        )
        if also:
            continue
        try:
            phone = customer_service.normalize_phone(sender)
        except ValidationError:
            phone = "+" + sender.lstrip("+")
        customer = db.query(Customer).filter(Customer.whatsapp_number == phone).first()
        record = WhatsAppMessage(
            customer_id=customer.id if customer else None,
            provider_message_id=provider_id,
            direction=MessageDirection.INBOUND.value,
            message_type=MessageType.INBOUND.value,
            status=MessageStatus.DELIVERED.value,
            idempotency_key=f"inbound:{provider_id}",
            phone_number=phone,
            body_preview=text[:500],
            request_payload={"text": text, "type": message.get("type")},
        )
        db.add(record)
        if customer and text.strip().lower() in OPT_OUT_WORDS:
            customer.consent_status = ConsentStatus.OPT_OUT.value
            db.flush()
            audit_service.log(
                db,
                action="whatsapp.opt_out",
                entity_type="CUSTOMER",
                entity_id=str(customer.id),
                after={"consent_status": ConsentStatus.OPT_OUT.value},
                commit=False,
            )
        changed += 1
    if changed:
        db.commit()
    return changed


def retry_failed(db, message_id: UUID) -> WhatsAppMessage:
    message = db.get(WhatsAppMessage, message_id)
    if not message:
        raise NotFoundError("Message not found")
    if message.status != MessageStatus.FAILED.value:
        raise ValidationError("Only failed messages can be retried")
    if not message.customer_id:
        raise ValidationError("Message has no customer to retry")
    customer = customer_service.get(db, message.customer_id)
    payload = message.request_payload or {}
    payload.pop("_meta", None)
    try:
        result = _post_message(db, payload)
        ids = result.get("messages", []) or []
        message.provider_message_id = ids[0].get("id") if ids else None
        message.status = MessageStatus.SENT.value
        message.sent_at = datetime.now(timezone.utc)
        message.error = None
    except ProviderError as exc:
        message.error = str(exc)[:500]
        message.retry_count += 1
    message.retry_count += 1
    db.commit()
    db.refresh(message)
    return message
