from pydantic import BaseModel, Field


class QaSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)


class SectionSearchRequest(BaseModel):
    section: str = Field(min_length=1)
    top_k_per_query: int = Field(default=5, ge=1, le=20)
