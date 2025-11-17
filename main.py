import os
from typing import List, Optional, Literal
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- Compliance policy ----
class Policy(BaseModel):
    minimum_age: int = Field(21, description="Minimum legal age required to purchase")
    allowed_categories: List[str] = Field(default_factory=lambda: ["bud", "vapes", "edibles"])

POLICY = Policy()

# ---- Product models ----
AllowedCategory = Literal['bud', 'vapes', 'edibles']

class ProductIn(BaseModel):
    title: str
    description: Optional[str] = None
    price: float
    category: AllowedCategory
    in_stock: bool = True
    thc_mg: Optional[float] = None
    cbd_mg: Optional[float] = None

class ProductOut(ProductIn):
    id: str


@app.get("/")
def read_root():
    return {"message": "Hello from FastAPI Backend!"}


@app.get("/policy", response_model=Policy)
def get_policy():
    """Return current age requirement and allowed product categories."""
    return POLICY


@app.get("/products", response_model=List[ProductOut])
def list_products(category: Optional[AllowedCategory] = Query(None, description="Filter by category")):
    """List products filtered by allowed categories, optionally by category."""
    try:
        from database import get_documents
    except Exception:
        # Database not configured; return empty list gracefully
        return []

    filter_q = {}
    if category:
        if category not in POLICY.allowed_categories:
            raise HTTPException(status_code=400, detail="Category not allowed by policy")
        filter_q["category"] = category
    else:
        # ensure only allowed categories
        filter_q["category"] = {"$in": POLICY.allowed_categories}

    docs = get_documents("product", filter_q)
    items: List[ProductOut] = []
    for d in docs:
        try:
            items.append(ProductOut(
                id=str(d.get("_id")),
                title=d.get("title"),
                description=d.get("description"),
                price=float(d.get("price", 0)),
                category=d.get("category"),
                in_stock=bool(d.get("in_stock", True)),
                thc_mg=d.get("thc_mg"),
                cbd_mg=d.get("cbd_mg"),
            ))
        except Exception:
            # skip invalid doc
            continue
    return items


@app.get("/api/hello")
def hello():
    return {"message": "Hello from the backend API!"}


@app.get("/test")
def test_database():
    """Test endpoint to check if database is available and accessible"""
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }
    
    try:
        # Try to import database module
        from database import db
        
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Configured"
            response["database_name"] = db.name if hasattr(db, 'name') else "✅ Connected"
            response["connection_status"] = "Connected"
            
            # Try to list collections to verify connectivity
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]  # Show first 10 collections
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️  Connected but Error: {str(e)[:50]}"
        else:
            response["database"] = "⚠️  Available but not initialized"
            
    except ImportError:
        response["database"] = "❌ Database module not found (run enable-database first)"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"
    
    # Check environment variables
    import os
    response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
    response["database_name"] = "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set"
    
    return response


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
