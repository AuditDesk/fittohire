from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.templating import Jinja2Templates
import os

app = FastAPI()

# Static files (CSS, JS, images)
app.mount("/static", StaticFiles(directory="static"), name="static")

# Register routers
from app.routers import auth, payments
app.include_router(auth.router)
app.include_router(payments.router)

# Existing pages
@app.get("/")
async def home():
    return FileResponse("static/index.html")

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
