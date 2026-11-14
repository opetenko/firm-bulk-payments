"""PayBulk use case: one READ COMMITTED transaction with idempotency (ADR 0001).

To be implemented in the use-case step. No retry loop: firms are locked in id
order, so deadlocks cannot happen; a lock or statement timeout surfaces as 503
and the client retries safely with the same Idempotency-Key.
"""
