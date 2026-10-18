from fastapi import FastAPI
from backend.routers import auth, students, menu, orders, tokens, admin, observations, analytics, optimization, simulation, pickup_slots, payment
from backend.core.config import settings
from backend.database.database import Base, engine

# Create database tables
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    debug=settings.DEBUG
)

# Include routers
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(students.router, prefix="/api/students", tags=["students"])
app.include_router(menu.router, prefix="/api/menu", tags=["menu"])
app.include_router(orders.router, prefix="/api/orders", tags=["orders"])
app.include_router(tokens.router, prefix="/api/tokens", tags=["tokens"])
app.include_router(admin.router, prefix="/api/admin", tags=["admin"])
app.include_router(observations.router, prefix="/api/admin/observations", tags=["observations"])
app.include_router(analytics.router, prefix="/api/admin/analytics", tags=["analytics"])
app.include_router(optimization.router, prefix="/api/admin/optimization", tags=["optimization"])
app.include_router(simulation.router, prefix="/api/admin/simulation", tags=["simulation"])
app.include_router(pickup_slots.router, prefix="/api/pickup-slots", tags=["pickup-slots"])
app.include_router(payment.router, prefix="/api/payment", tags=["payment"])

@app.get("/health", tags=["health"])
def health_check():
    return {"status": "ok", "message": "Smart Canteen API is running"}