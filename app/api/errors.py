"""Map exceptions to the HTTP responses of ADR 0001.

- Request validation (body or header) -> 400. FastAPI's default is 422, which the
  spec reserves for business errors, so it is overridden here.
- DomainError -> 400 for idempotency_key_reuse, 422 otherwise; body {"error": code}.
- ServiceBusy -> 503 {"error": "busy"} with Retry-After.
"""

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.application.pay_bulk import ServiceBusy
from app.domain.bulk_payment import DomainError, IdempotencyKeyReuse

RETRY_AFTER_SECONDS = "1"

DOMAIN_ERROR_STATUS: dict[type[DomainError], int] = {IdempotencyKeyReuse: 400}


class InvalidIdempotencyKey(Exception):
    """The Idempotency-Key header is missing or malformed."""


async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={"error": "invalid_request", "detail": jsonable_encoder(exc.errors())},
    )


async def _invalid_idempotency_key(_: Request, __: InvalidIdempotencyKey) -> JSONResponse:
    return JSONResponse(status_code=400, content={"error": "invalid_idempotency_key"})


async def _domain_error(_: Request, exc: DomainError) -> JSONResponse:
    status = DOMAIN_ERROR_STATUS.get(type(exc), 422)
    return JSONResponse(status_code=status, content={"error": exc.code})


async def _service_busy(_: Request, __: ServiceBusy) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={"error": "busy"},
        headers={"Retry-After": RETRY_AFTER_SECONDS},
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(InvalidIdempotencyKey, _invalid_idempotency_key)
    app.add_exception_handler(DomainError, _domain_error)
    app.add_exception_handler(ServiceBusy, _service_busy)
