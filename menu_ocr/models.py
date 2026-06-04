"""Pydantic models for post-LLM validation.

JSON Schema cannot express cross-field constraints (kind=1 ⇒ prices required,
kind=5 ⇒ sub-foods[] required, CHOOSE sub-foods must be kind=1, …).
Those rules live here and run AFTER the LLM call.

Terminology — aligned with hq-qrcode-admin Food model:
- COMMON (kind=1): standalone dish with price + optional beilages.
- CHOOSE (kind=5): parent header grouping variant sub-foods. Parent has NO
  price; each sub-food is itself kind=1 with own price. NOT a "combo bundle"
  — sub-foods are variants of the same dish concept (e.g. Hanoi Summer with
  Tofu / Huhn / Garnelen variants).
"""
from typing import List, Literal, Optional

from menu_ocr.config import KIND_CHOOSE, KIND_COMMON

try:
    from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
    PYDANTIC_OK = True
except ImportError:
    PYDANTIC_OK = False
    BaseModel = object  # type: ignore
    ValidationError = Exception  # type: ignore


if PYDANTIC_OK:
    # ====================================================
    # Reusable beilage modifier item (top-level food_conditions[])
    # ====================================================
    class FoodCondition(BaseModel):
        name: str = Field(..., min_length=1)
        base_price: float = Field(..., ge=0)
        plu: Optional[str] = None
        status: Optional[int] = 1

        @field_validator("name")
        @classmethod
        def _name_not_empty(cls, v):
            if not v.strip():
                raise ValueError("food_condition name is empty")
            return v.strip()

    # ====================================================
    # One option inside an OptionGroup/beilage (per-food inline price)
    # ====================================================
    class FoodData(BaseModel):
        name_food: str = Field(..., min_length=1)
        price: float = Field(..., ge=0)
        plu: Optional[str] = None
        required: Optional[bool] = False
        status: Optional[int] = 1

        @field_validator("name_food")
        @classmethod
        def _name_not_empty(cls, v):
            if not v.strip():
                raise ValueError("food_data name_food is empty")
            return v.strip()

    # ====================================================
    # OptionGroup — beilage modifier group attached to a Food
    # ====================================================
    class OptionGroup(BaseModel):
        name: str = Field(..., min_length=1)
        # 0 = single_choice, 1 = multi_choice
        type: Literal[0, 1]
        # 0 = optional, 1 = required (group-level)
        option: Literal[0, 1]
        food_datas: List[FoodData] = Field(..., min_length=1)

        @field_validator("name")
        @classmethod
        def _gname_not_empty(cls, v):
            if not v.strip():
                raise ValueError("option_group name is empty")
            return v.strip()

    # ====================================================
    # CHOOSE sub-food — Food that lives INSIDE a kind=5 parent.
    # Locked to kind=1 (no nested CHOOSE) — structurally cannot nest.
    # ====================================================
    class ChooseSubFood(BaseModel):
        name: str = Field(..., min_length=1)
        plu: Optional[str] = None
        type: Literal[1, 2]
        # Locked: CHOOSE sub-foods are always kind=1.
        kind: Literal[1] = 1
        price_in: float = Field(..., ge=0)
        price_out: float = Field(..., ge=0)
        # Sub-foods can have their own beilages (size/topping on a specific variant).
        options: List[OptionGroup] = Field(default_factory=list)

        @field_validator("name")
        @classmethod
        def _name_not_empty(cls, v):
            if not v.strip():
                raise ValueError("CHOOSE sub-food name is empty")
            return v.strip()

    # ====================================================
    # Food — top-level menu row (standalone COMMON or CHOOSE parent)
    # ====================================================
    class Food(BaseModel):
        name: str = Field(..., min_length=1)
        plu: Optional[str] = None
        type: Literal[1, 2]
        kind: Literal[1, 5]
        price_in: Optional[float] = Field(default=None, ge=0)
        price_out: Optional[float] = Field(default=None, ge=0)
        sale_off_percent: Optional[float] = Field(default=None, ge=0, le=100)
        description: Optional[str] = None
        product_info: Optional[str] = None
        # Beilages on this food (size/topping/required-choice).
        options: List[OptionGroup] = Field(default_factory=list)
        # When kind=5 (CHOOSE): list of variant sub-foods.
        # When kind=1: must be None.
        foods: Optional[List[ChooseSubFood]] = None

        @field_validator("name")
        @classmethod
        def _name_not_empty(cls, v):
            if not v.strip():
                raise ValueError("food name is empty")
            return v.strip()

        @model_validator(mode="after")
        def _shape_by_kind(self):
            if self.kind == KIND_COMMON:
                if self.price_in is None or self.price_out is None:
                    raise ValueError("kind=1 (COMMON) requires both price_in and price_out")
                if self.foods:
                    raise ValueError("kind=1 (COMMON) must NOT have CHOOSE sub-foods[]")
            elif self.kind == KIND_CHOOSE:
                if self.price_in is not None or self.price_out is not None:
                    raise ValueError(
                        "kind=5 (CHOOSE) parent must have price_in=null and price_out=null "
                        "(parent header has no printed price)"
                    )
                if not self.foods or len(self.foods) < 1:
                    raise ValueError(
                        "kind=5 (CHOOSE) requires foods[] with at least 1 sub-food"
                    )
            return self

    # ====================================================
    # Group (menu section)
    # ====================================================
    class Group(BaseModel):
        name: str = Field(..., min_length=1)
        description: Optional[str] = None
        foods: List[Food] = Field(..., min_length=1)

        @field_validator("name")
        @classmethod
        def _name_not_empty(cls, v):
            if not v.strip():
                raise ValueError("group name is empty")
            return v.strip()

    # ====================================================
    # Top-level extraction
    # ====================================================
    class MenuExtraction(BaseModel):
        food_conditions: List[FoodCondition] = Field(default_factory=list)
        groups: List[Group] = Field(..., min_length=1)

        @model_validator(mode="after")
        def _unique_food_conditions(self):
            seen = {}
            for fc in self.food_conditions:
                key = fc.name.lower()
                if key in seen:
                    raise ValueError(f"food_conditions has duplicate name: '{fc.name}'")
                seen[key] = fc
            return self
