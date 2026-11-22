from pydantic import BaseModel, Field


class PaperParameters(BaseModel, frozen=True):
    # Explicit simulation assumptions in parts per million of fill notional.
    slippage_bps: int = Field(default=5, ge=0, le=1000)
    volume_bps: int = Field(default=50, ge=1, le=10000)
    brokerage_ppm: int = Field(default=300, ge=0, le=100000)
    brokerage_cap_paisa: int = Field(default=2000, ge=0, le=100000)
    buy_tax_ppm: int = Field(default=1000, ge=0, le=100000)
    sell_tax_ppm: int = Field(default=1000, ge=0, le=100000)
    exchange_ppm: int = Field(default=30, ge=0, le=100000)
    regulator_ppm: int = Field(default=1, ge=0, le=100000)
    stamp_ppm: int = Field(default=150, ge=0, le=100000)
    gst_bps: int = Field(default=1800, ge=0, le=10000)
