"""All SQL of the bulk payment transaction (ADR 0001, "Transaction").

To be implemented in the store step:
    lock_firms(conn, uuids)          SELECT ... ORDER BY id FOR NO KEY UPDATE
    find_batch(conn, payer_id, key)  existing batch for the idempotency key, if any
    insert_batch(conn, ...)          INSERT payment_batches ... RETURNING id
    insert_payments(conn, ...)       one INSERT, ids returned in request order
    apply_balance_deltas(conn, ...)  UPDATE firms SET balance_cents = balance_cents + delta
"""
