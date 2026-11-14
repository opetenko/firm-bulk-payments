from fastapi import FastAPI

from app.api.errors import register_error_handlers
from app.api.routes import router
from app.application.pay_bulk import PayBulk


def create_app(pay_bulk: PayBulk | None = None) -> FastAPI:
    """Composition root: the only place that wires infrastructure into the API."""
    if pay_bulk is None:
        from app.infrastructure.db import engine

        pay_bulk = PayBulk(engine)

    app = FastAPI(title="Firm Bulk Payments")
    app.state.pay_bulk = pay_bulk
    register_error_handlers(app)
    app.include_router(router)
    return app


app = create_app()
