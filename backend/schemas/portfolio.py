from pydantic import BaseModel, Field, model_validator, ValidationError
from datetime import date, datetime
from uuid import UUID
from typing import Optional


class HoldingCreate(BaseModel):
    symbol: str
    name: str
    asset_type: str = "stock"
    quantity: float = 0.0
    buy_price: float = 0.0
    buy_date: date = Field(default_factory=date.today)

    @model_validator(mode="before")
    @classmethod
    def normalize_asset_type(cls, data: dict) -> dict:
        """Accept legacy `item_type` field as an alias for `asset_type`."""
        if isinstance(data, dict) and "item_type" in data and "asset_type" not in data:
            data["asset_type"] = data.pop("item_type")
        return data

    @model_validator(mode="after")
    def validate_buy_date(self) -> "HoldingCreate":
        """Validate buy_date: must be in the past, no dates before 2000."""
        min_date = date(2000, 1, 1)
        today = date.today()

        if self.buy_date >= today:
            raise ValueError("Buy date must be in the past")
        if self.buy_date < min_date:
            raise ValueError("Buy date cannot be before January 1, 2000")

        return self


class HoldingResponse(BaseModel):
    id: UUID
    user_id: str
    symbol: str
    name: str
    asset_type: str
    buy_price: float
    quantity: float
    buy_date: date
    created_at: Optional[datetime] = None
    added_at: Optional[datetime] = None   # exposed alias for created_at

    model_config = {"from_attributes": True}

    @model_validator(mode="after")
    def populate_added_at(self) -> "HoldingResponse":
        """Mirror created_at → added_at so the frontend can use either field."""
        if self.added_at is None and self.created_at is not None:
            self.added_at = self.created_at
        return self
