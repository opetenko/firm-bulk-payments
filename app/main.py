from fastapi import FastAPI

from app.api.routes import router

app = FastAPI(title="Firm Bulk Payments")
app.include_router(router)
