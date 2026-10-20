from pydantic import BaseModel, ConfigDict, Field


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str
    password: str
    invite_code: str


class LoginRequest(BaseModel):
    username: str
    password: str


class GitHubLoginRequest(BaseModel):
    next: str = Field(default="/", max_length=2048)


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    role: str


class CurrentUserResponse(BaseModel):
    username: str
    role: str
