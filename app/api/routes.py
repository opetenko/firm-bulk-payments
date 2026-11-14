from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, Response

from app.api.errors import InvalidIdempotencyKey
from app.api.schemas import BulkPaymentRequest, BulkPaymentResponse, validate_idempotency_key
from app.application.pay_bulk import PayBulk

router = APIRouter()


def get_pay_bulk(request: Request) -> PayBulk:
    """The use case built by create_app(): the API never touches the engine itself."""
    pay_bulk = request.app.state.pay_bulk  # app.state is untyped: check, don't trust
    if not isinstance(pay_bulk, PayBulk):
        raise RuntimeError("app.state.pay_bulk is not configured; build the app with create_app()")
    return pay_bulk


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# A sync `def`: FastAPI runs it in a worker thread, so the blocking database
# transaction never stalls the event loop.
@router.post("/bulk-payments", status_code=201, response_model=BulkPaymentResponse)
def create_bulk_payment(
    body: BulkPaymentRequest,
    response: Response,
    pay_bulk: Annotated[PayBulk, Depends(get_pay_bulk)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> BulkPaymentResponse:
    try:
        key = validate_idempotency_key(idempotency_key)
    except ValueError as exc:
        raise InvalidIdempotencyKey() from exc

    result = pay_bulk(body.to_command(key))

    if result.replayed:
        response.headers["Idempotent-Replayed"] = "true"
    return BulkPaymentResponse(
        batch_uuid=result.batch_uuid,
        payment_ids=result.payment_ids,
        total_cents=result.total_cents,
    )
