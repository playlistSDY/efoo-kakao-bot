from typing import Any

from app.models.chats import ChatMessage, ChatSession
from app.models.meals import Meal, MealFetchLog
from app.models.restaurants import Restaurant
from app.models.users import UserProfile

# Backward-compatible placeholders used by the legacy monthly fetch script.
Rating: Any = None
Keyword: Any = None
MealKeywordReview: Any = None

__all__ = [
    "ChatMessage",
    "ChatSession",
    "Keyword",
    "Meal",
    "MealFetchLog",
    "MealKeywordReview",
    "Rating",
    "Restaurant",
    "UserProfile",
]
