from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

from app.routers import auth, payments, dashboard, interview
app.include_router(auth.router)
app.include_router(payments.router)
app.include_router(dashboard.router)
app.include_router(interview.router)

@app.get("/")
async def home():
    return FileResponse("static/index.html")

@app.get("/pricing")
async def pricing():
    # Redirect to landing page pricing section
    from fastapi.responses import RedirectResponse
    return RedirectResponse("/#pricing")

@app.get("/privacy")
async def privacy():
    return FileResponse("static/privacy.html")

@app.get("/terms")
async def terms():
    return FileResponse("static/terms.html")

@app.get("/refund")
async def refund():
    return FileResponse("static/refund.html")

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
