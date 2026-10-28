"""Request validation models (Pydantic)."""
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TxType(str, Enum):
    credit = "credit"
    debit = "debit"


def _clean_text(value: str, field: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError(f"{field} must not be blank")
    return value


class AccountCreate(BaseModel):
    # extra="forbid": a client sending "balance" (or anything unknown) is rejected,
    # so the initial balance can never be set from outside.
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, v: str) -> str:
        return _clean_text(v, "name")


class TransactionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: TxType
    amount: Decimal = Field(gt=0, max_digits=15, decimal_places=2)
    description: str = Field(default="", max_length=500)
    reference_id: str = Field(min_length=1, max_length=100)

    @field_validator("reference_id")
    @classmethod
    def reference_not_blank(cls, v: str) -> str:
        return _clean_text(v, "reference_id")

    @field_validator("description")
    @classmethod
    def description_strip(cls, v: str) -> str:
        return v.strip()


class TransferCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_account_id: str = Field(min_length=1)
    destination_account_id: str = Field(min_length=1)
    amount: Decimal = Field(gt=0, max_digits=15, decimal_places=2)
    description: str = Field(default="", max_length=500)
    reference_id: str = Field(min_length=1, max_length=100)

    @field_validator("reference_id")
    @classmethod
    def reference_not_blank(cls, v: str) -> str:
        return _clean_text(v, "reference_id")

    @field_validator("description")
    @classmethod
    def description_strip(cls, v: str) -> str:
        return v.strip()
