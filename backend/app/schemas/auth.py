from pydantic import BaseModel


class LoginRequest(BaseModel):
    login: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class AdminUserResponse(BaseModel):
    id: str
    login: str
    role: str
    tenant_id: str | None = None
    permissions: list[str] = []
    is_active: bool

    model_config = {"from_attributes": True}
