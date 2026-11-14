from fastapi import FastAPI

app = FastAPI(title="Firm Bulk Payments")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
