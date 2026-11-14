"""POST /bulk-payments against the response table of ADR 0001."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, func, select

from app.application.pay_bulk import PayBulk
from app.infrastructure import store
from app.infrastructure.tables import firms, payment_batches
from app.main import create_app
from tests.conftest import TEST_DATABASE_URL

PINECREST = "3f1c9a2e-7b4d-4c1e-9a55-2d8e6f0b7c41"
LOPEZ = "8b2e4c71-0d3a-4f6e-b1c9-5a7d2e9f4c10"
NAIR = "e5f18b3c-2a9d-4c07-8e6b-1d4a7f9c3b25"
UNKNOWN = "00000000-0000-4000-8000-000000000000"

SPEC_BODY = {
    "payer_firm_uuid": PINECREST,
    "payments": [
        {"amount": "6250", "payee_firm_uuid": NAIR, "description": "Overflow returns, August 2026"},
        {"amount": "5800.5", "payee_firm_uuid": NAIR, "description": "Amended returns, August 2026"},
        {"amount": "1200.75", "payee_firm_uuid": LOPEZ, "description": "Bookkeeping cleanup, 3 clients"},
    ],
}


def body(*payments: tuple[str, str], payer: str = PINECREST) -> dict:
    return {
        "payer_firm_uuid": payer,
        "payments": [{"amount": a, "payee_firm_uuid": p, "description": "x"} for a, p in payments],
    }


def balances(engine: Engine) -> dict[str, int]:
    with engine.connect() as conn:
        return dict(conn.execute(select(firms.c.uuid, firms.c.balance_cents)).all())


def batch_count(engine: Engine) -> int:
    with engine.connect() as conn:
        return conn.scalar(select(func.count()).select_from(payment_batches))


@pytest.fixture
def client(clean_engine: Engine) -> TestClient:
    return TestClient(create_app(PayBulk(clean_engine, lock_timeout_ms=100)))


def post(client: TestClient, json: object, key: str | None = "key-1", **kwargs):
    headers = {} if key is None else {"Idempotency-Key": key}
    return client.post("/bulk-payments", json=json, headers=headers, **kwargs)


# --- 201 ----------------------------------------------------------------------


def test_spec_example_returns_201_and_moves_money(client: TestClient, clean_engine: Engine) -> None:
    response = post(client, SPEC_BODY)

    assert response.status_code == 201
    data = response.json()
    assert set(data) == {"batch_uuid", "payment_ids", "total_cents"}
    assert data["total_cents"] == 1_325_125
    assert len(data["payment_ids"]) == 3
    assert "Idempotent-Replayed" not in response.headers
    assert balances(clean_engine) == {PINECREST: 3_674_875, LOPEZ: 170_075, NAIR: 1_405_050}


def test_replay_returns_identical_body_with_replayed_header(
    client: TestClient, clean_engine: Engine
) -> None:
    first = post(client, SPEC_BODY)

    second = post(client, SPEC_BODY)

    assert second.status_code == 201
    assert second.content == first.content
    assert second.headers["Idempotent-Replayed"] == "true"
    assert batch_count(clean_engine) == 1


def test_replay_with_equivalent_spelling_is_still_a_replay(client: TestClient) -> None:
    post(client, body(("300", NAIR)))

    response = post(client, body(("300.00", NAIR.upper())))

    assert response.status_code == 201
    assert response.headers["Idempotent-Replayed"] == "true"


# --- business errors ------------------------------------------------------------


def test_same_key_different_body_returns_400(client: TestClient, clean_engine: Engine) -> None:
    post(client, SPEC_BODY)

    response = post(client, body(("1", NAIR)))

    assert response.status_code == 400
    assert response.json() == {"error": "idempotency_key_reuse"}
    assert batch_count(clean_engine) == 1


@pytest.mark.parametrize(
    ("request_body", "error"),
    [
        pytest.param(body(("500.01", NAIR), payer=LOPEZ), "insufficient_funds", id="insufficient_funds"),
        pytest.param(body(("1", NAIR), payer=UNKNOWN), "unknown_firm", id="unknown_payer"),
        pytest.param(body(("1", UNKNOWN)), "unknown_firm", id="unknown_payee"),
        pytest.param(body(("1", NAIR), ("1", PINECREST)), "self_payment", id="self_payment"),
    ],
)
def test_business_error_returns_422_and_writes_nothing(
    client: TestClient, clean_engine: Engine, request_body: dict, error: str
) -> None:
    before = balances(clean_engine)

    response = post(client, request_body)

    assert response.status_code == 422
    assert response.json() == {"error": error}
    assert balances(clean_engine) == before
    assert batch_count(clean_engine) == 0


# --- 400 --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "request_body",
    [
        pytest.param({**SPEC_BODY, "payments": []}, id="empty_payments"),
        pytest.param(body(*[("1", NAIR)] * 1001), id="oversized_payments"),
        pytest.param(body(("1.234", NAIR)), id="bad_amount_format"),
        pytest.param(body(("0", NAIR)), id="zero_amount"),
        pytest.param({**SPEC_BODY, "payer_firm_uuid": 42}, id="wrong_type"),
        pytest.param(
            {**SPEC_BODY, "payments": [{"amount": 300, "payee_firm_uuid": NAIR, "description": "x"}]},
            id="amount_as_number",
        ),
        pytest.param([SPEC_BODY], id="not_an_object"),
    ],
)
def test_invalid_body_returns_400(client: TestClient, clean_engine: Engine, request_body: object) -> None:
    response = post(client, request_body)

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"
    assert batch_count(clean_engine) == 0


def test_malformed_json_returns_400(client: TestClient) -> None:
    response = client.post(
        "/bulk-payments",
        content=b'{"payer_firm_uuid": ',
        headers={"Idempotency-Key": "k", "Content-Type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"


@pytest.mark.parametrize(
    "key",
    [pytest.param(None, id="missing"), pytest.param("", id="empty"), pytest.param("x" * 256, id="too_long")],
)
def test_bad_idempotency_key_returns_400(client: TestClient, clean_engine: Engine, key: str | None) -> None:
    response = post(client, SPEC_BODY, key=key)

    assert response.status_code == 400
    assert response.json() == {"error": "invalid_idempotency_key"}
    assert batch_count(clean_engine) == 0


# --- 503 --------------------------------------------------------------------------


def test_lock_timeout_returns_503_with_retry_after(client: TestClient, clean_engine: Engine) -> None:
    with clean_engine.connect() as other:
        other.begin()
        store.lock_firms(other, [NAIR])

        response = post(client, SPEC_BODY)
        other.rollback()

    assert response.status_code == 503
    assert response.json() == {"error": "busy"}
    assert response.headers["Retry-After"] == "1"

    # Nothing was written, so the client's retry with the same key succeeds.
    assert post(client, SPEC_BODY).status_code == 201


def test_exhausted_connection_pool_returns_503_not_500(clean_engine: Engine) -> None:
    tiny = create_engine(TEST_DATABASE_URL, pool_size=1, max_overflow=0, pool_timeout=0.2)
    client = TestClient(create_app(PayBulk(tiny)), raise_server_exceptions=False)
    try:
        held = tiny.connect()  # every connection of this replica is in use

        response = post(client, SPEC_BODY)

        assert response.status_code == 503
        assert response.json() == {"error": "busy"}
        assert response.headers["Retry-After"] == "1"

        held.close()
        assert post(client, SPEC_BODY).status_code == 201  # retry once a connection is free
    finally:
        tiny.dispose()
