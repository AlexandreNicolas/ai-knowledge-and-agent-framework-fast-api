from pydantic import BaseModel, Field
from typing import Optional


class UserPreferences(BaseModel):
    user_name: Optional[str] = None
    industry: Optional[str] = None
    project_type: Optional[str] = None
    company_size: Optional[str] = None
    interests: Optional[list[str]] = None


class AskRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    conversation_id: Optional[str] = None
    user_preferences: Optional[UserPreferences] = None
