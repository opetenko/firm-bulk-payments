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

_RETRY_AFTER_SECONDS = "1"

_DOMAIN_ERROR_STATUS: dict[type[DomainError], int] = {IdempotencyKeyReuse: 400}


class InvalidIdempotencyKey(Exception):
    """The Idempotency-Key header is missing or malformed."""


def _expect[E: Exception](exc: Exception, kind: type[E]) -> E:
    """Narrow the handler argument to the type the handler was registered for.

    Starlette types every handler as (Request, Exception). A checked narrowing
    instead of cast(): a handler registered for the wrong type fails loudly.
    """
    if not isinstance(exc, kind):
        raise TypeError(f"handler for {kind.__name__} received {type(exc).__name__}")
    return exc


async def _validation_error(_: Request, exc: Exception) -> JSONResponse:
    errors = _expect(exc, RequestValidationError).errors()
    return JSONResponse(
        status_code=400,
        content={"error": "invalid_request", "detail": jsonable_encoder(errors)},
    )


async def _invalid_idempotency_key(_: Request, __: Exception) -> JSONResponse:
    return JSONResponse(status_code=400, content={"error": "invalid_idempotency_key"})


async def _domain_error(_: Request, exc: Exception) -> JSONResponse:
    error = _expect(exc, DomainError)
    status = _DOMAIN_ERROR_STATUS.get(type(error), 422)
    return JSONResponse(status_code=status, content={"error": error.code})


async def _service_busy(_: Request, __: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={"error": "busy"},
        headers={"Retry-After": _RETRY_AFTER_SECONDS},
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(InvalidIdempotencyKey, _invalid_idempotency_key)
    app.add_exception_handler(DomainError, _domain_error)
    app.add_exception_handler(ServiceBusy, _service_busy)
