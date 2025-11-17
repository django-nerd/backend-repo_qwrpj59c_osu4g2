import os
import uuid
from typing import List, Optional, Literal
from fastapi import FastAPI, HTTPException, Query, Request, Response, Body
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from pydantic import BaseModel, Field

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Session cookie (signed) for server-side age verification and carts
SESSION_SECRET = os.getenv("SESSION_SECRET", "change-me-in-prod")
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, session_cookie="gl_session")

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

# Cart and order models
class CartItem(BaseModel):
    product_id: str
    qty: int = Field(1, ge=1, le=100)

class CartOut(BaseModel):
    items: List[CartItem]
    subtotal: float

class OrderOut(BaseModel):
    id: str
    items: List[CartItem]
    subtotal: float
    status: str = "created"


def ensure_sid(request: Request, response: Response) -> str:
    sid = request.cookies.get("sid")
    if not sid:
        sid = uuid.uuid4().hex
        # httpOnly cart/session id cookie
        response.set_cookie("sid", sid, httponly=True, samesite="lax")
    return sid


@app.get("/")
def read_root():
    return {"message": "Hello from FastAPI Backend!"}


@app.get("/policy", response_model=Policy)
def get_policy():
    """Return current age requirement and allowed product categories."""
    return POLICY


@app.post("/verify-age")
def verify_age(request: Request):
    """Sets a signed session flag indicating 21+ verification."""
    request.session["age_verified_21"] = True
    return {"ok": True, "age_verified": True}


@app.get("/auth/status")
def auth_status(request: Request):
    return {"age_verified": bool(request.session.get("age_verified_21"))}


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


@app.post("/products", response_model=dict)
def create_product(payload: ProductIn):
    """Create a new product."""
    try:
        from database import create_document
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database not available: {e}")

    prod_id = create_document("product", payload)
    return {"id": prod_id}


@app.put("/products/{product_id}", response_model=dict)
def update_product(product_id: str, payload: ProductIn):
    try:
        from database import db
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database not available: {e}")

    from bson import ObjectId
    try:
        oid = ObjectId(product_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid product id")

    doc = payload.model_dump()
    doc["updated_at"] = __import__("datetime").datetime.utcnow()
    result = db["product"].update_one({"_id": oid}, {"$set": doc})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Product not found")
    return {"ok": True}


@app.get("/cart", response_model=CartOut)
def get_cart(request: Request, response: Response):
    try:
        from database import db
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database not available: {e}")

    sid = ensure_sid(request, response)
    cart = db["cart"].find_one({"session_id": sid}) or {"items": []}

    # compute subtotal by looking up product prices
    subtotal = 0.0
    items = []
    from bson import ObjectId
    for it in cart.get("items", []):
        pid = it.get("product_id")
        qty = int(it.get("qty", 1))
        try:
            prod = db["product"].find_one({"_id": ObjectId(pid)})
            if prod and prod.get("in_stock", True):
                subtotal += float(prod.get("price", 0)) * qty
                items.append({"product_id": pid, "qty": qty})
        except Exception:
            continue

    return CartOut(items=[CartItem(**i) for i in items], subtotal=round(subtotal, 2))


@app.post("/cart/items", response_model=CartOut)
def add_to_cart(request: Request, response: Response, item: CartItem = Body(...)):
    try:
        from database import db
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database not available: {e}")

    sid = ensure_sid(request, response)

    cart = db["cart"].find_one({"session_id": sid})
    if not cart:
        cart = {"session_id": sid, "items": []}

    # upsert item by product_id
    updated = False
    for it in cart["items"]:
        if it.get("product_id") == item.product_id:
            it["qty"] = item.qty
            updated = True
            break
    if not updated:
        cart["items"].append({"product_id": item.product_id, "qty": item.qty})

    cart["updated_at"] = __import__("datetime").datetime.utcnow()
    db["cart"].update_one({"session_id": sid}, {"$set": cart}, upsert=True)

    return get_cart(request, response)


@app.delete("/cart/items/{product_id}", response_model=CartOut)
def remove_from_cart(product_id: str, request: Request, response: Response):
    try:
        from database import db
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database not available: {e}")

    sid = ensure_sid(request, response)
    cart = db["cart"].find_one({"session_id": sid}) or {"items": []}
    cart["items"] = [it for it in cart["items"] if it.get("product_id") != product_id]
    cart["updated_at"] = __import__("datetime").datetime.utcnow()
    db["cart"].update_one({"session_id": sid}, {"$set": cart}, upsert=True)
    return get_cart(request, response)


@app.post("/checkout", response_model=OrderOut)
def checkout(request: Request, response: Response):
    """Create an order from the current cart. Requires age verification."""
    if not request.session.get("age_verified_21"):
        raise HTTPException(status_code=403, detail="Age verification required")

    try:
        from database import db, create_document
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database not available: {e}")

    sid = ensure_sid(request, response)
    cart = db["cart"].find_one({"session_id": sid}) or {"items": []}
    # compute subtotal
    subtotal_resp = get_cart(request, response)
    subtotal = subtotal_resp.subtotal

    # create order document
    order_doc = {
        "session_id": sid,
        "items": cart.get("items", []),
        "subtotal": subtotal,
        "status": "created",
    }
    order_id = create_document("order", order_doc)

    # clear cart
    db["cart"].delete_one({"session_id": sid})

    return OrderOut(id=order_id, items=[CartItem(**i) for i in order_doc["items"]], subtotal=subtotal, status="created")


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
