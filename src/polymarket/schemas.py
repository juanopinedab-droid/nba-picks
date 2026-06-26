from pydantic import BaseModel, Field, field_validator, Discriminator
from typing import Literal, Annotated


class BaseChart(BaseModel):
    type: str
    title: str = Field(max_length=120)


class LineChart(BaseChart):
    type: Literal["line_chart"]
    xAxisLabel: str = "Date"
    yAxisLabel: str = "Price"
    data: list[dict[str, float | str]] = Field(max_length=30)

    @field_validator("data")
    @classmethod
    def check_data_points(cls, v):
        if len(v) == 0:
            raise ValueError("line_chart requires at least 1 data point")
        if len(v) > 30:
            raise ValueError("line_chart max 30 data points")
        first_keys = set(k for k in v[0].keys() if k != "name")
        if not first_keys:
            raise ValueError("line_chart data must have at least one series key besides 'name'")
        return v


class BarChart(BaseChart):
    type: Literal["bar_chart"]
    xAxisLabel: str = "Metric"
    data: list[dict[str, float | str]] = Field(max_length=20)

    @field_validator("data")
    @classmethod
    def check_bar_data(cls, v):
        if len(v) == 0:
            raise ValueError("bar_chart requires at least 1 data point")
        if len(v) > 20:
            raise ValueError("bar_chart max 20 data points")
        return v


class DonutChart(BaseChart):
    type: Literal["donut_chart"]
    data: list[dict[str, float | str]] = Field(min_length=2, max_length=8)

    @field_validator("data")
    @classmethod
    def check_donut_fields(cls, v):
        for d in v:
            if "label" not in d:
                raise ValueError("donut_chart data entries must have 'label' field")
            val = d.get("value", 0)
            if not isinstance(val, (int, float)) or val < 0:
                raise ValueError(f"donut_chart 'value' must be >= 0, got {val}")
        total = sum(d.get("value", 0) for d in v if isinstance(d.get("value"), (int, float)))
        if total <= 0:
            raise ValueError("donut_chart values must sum > 0")
        return v


class DepthChart(BaseChart):
    type: Literal["depth_chart"]
    data: list[dict] = Field(max_length=40)

    @field_validator("data")
    @classmethod
    def check_depth_fields(cls, v):
        if len(v) < 2:
            raise ValueError("depth_chart requires at least 2 data points")
        for d in v:
            if "price" not in d:
                raise ValueError("depth_chart requires 'price' field")
            if "bid_size" not in d and "ask_size" not in d:
                raise ValueError("depth_chart requires 'bid_size' or 'ask_size' field")
        return v


ChartType = Annotated[
    LineChart | BarChart | DonutChart | DepthChart,
    Discriminator("type")
]


class SynthesisResult(BaseModel):
    fundamental_shift: float = Field(ge=-0.20, le=0.20)
    rationale: str = Field(min_length=10, max_length=4000)
    top_reports: list[int] = Field(default_factory=list, max_length=5)
    visualizations: list[ChartType] = Field(default_factory=list, max_length=4)
