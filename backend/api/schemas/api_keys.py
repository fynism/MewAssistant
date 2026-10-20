from pydantic import BaseModel, Field


class KeyInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
