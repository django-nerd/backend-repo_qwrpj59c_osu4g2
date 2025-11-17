"""
Database Schemas

Define your MongoDB collection schemas here using Pydantic models.
These schemas are used for data validation in your application.

Each Pydantic model represents a collection in your database.
Model name is converted to lowercase for the collection name:
- User -> "user" collection
- Product -> "product" collection
- BlogPost -> "blogs" collection
"""

from pydantic import BaseModel, Field
from typing import Optional, Literal

# Compliance policy (used by server responses, not a collection)
class Policy(BaseModel):
    minimum_age: int = Field(21, description="Minimum legal age required to purchase")
    allowed_categories: list[str] = Field(
        default_factory=lambda: ["bud", "vapes", "edibles"],
        description="Whitelisted product categories"
    )

class User(BaseModel):
    """
    Users collection schema
    Collection name: "user" (lowercase of class name)
    """
    name: str = Field(..., description="Full name")
    email: str = Field(..., description="Email address")
    address: str = Field(..., description="Address")
    age: Optional[int] = Field(None, ge=0, le=120, description="Age in years")
    is_active: bool = Field(True, description="Whether user is active")

class Product(BaseModel):
    """
    Products collection schema
    Collection name: "product" (lowercase of class name)
    """
    title: str = Field(..., description="Product title")
    description: Optional[str] = Field(None, description="Product description")
    price: float = Field(..., ge=0, description="Price in dollars")
    category: Literal['bud', 'vapes', 'edibles'] = Field(..., description="Product category")
    in_stock: bool = Field(True, description="Whether product is in stock")
    thc_mg: Optional[float] = Field(None, ge=0, description="THC milligrams per unit (if applicable)")
    cbd_mg: Optional[float] = Field(None, ge=0, description="CBD milligrams per unit (if applicable)")
