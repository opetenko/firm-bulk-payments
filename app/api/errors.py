"""Exception handlers, to be added in the endpoint step:

- RequestValidationError -> 400 (FastAPI's default is 422, which the spec reserves
  for business errors).
- DomainError -> 422 / 400 by error code, body {"error": code}.
- Lock or statement timeout -> 503 {"error": "busy"} with Retry-After.
"""
