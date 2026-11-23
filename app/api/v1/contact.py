from fastapi import APIRouter, BackgroundTasks, Depends

from app.api import rate_limit as limits
from app.api.rate_limit import rate_limit
from app.schemas.common import MessageResponse
from app.schemas.contact import ContactRequest
from app.services.contact import check_daily_limit, contact_recipient, send_contact_message

router = APIRouter()

SENT = MessageResponse(message="Thank you, your message has been sent.")


@router.post(
    "",
    response_model=MessageResponse,
    status_code=202,
    responses={
        422: {"description": "A field is missing, too long or not allowed"},
        429: limits.TOO_MANY_REQUESTS,
        503: {
            "description": 'The contact form is not configured (`code` is "contact_unavailable")'
        },
    },
    dependencies=[Depends(rate_limit(limits.CONTACT))],
)  # type: ignore[misc]
async def send_message(data: ContactRequest, background_tasks: BackgroundTasks) -> MessageResponse:
    """Send a message to the team.

    The message is emailed to an address kept only in the server configuration, with
    `Reply-To` set to the sender. No confirmation is sent to the given address, so the form
    cannot be used to send emails to other people.
    """
    recipient = contact_recipient()
    if data.is_bot:
        # Same answer as for a real message, so the bot does not learn it was caught
        return SENT
    await check_daily_limit()
    background_tasks.add_task(send_contact_message, recipient, data)
    return SENT
