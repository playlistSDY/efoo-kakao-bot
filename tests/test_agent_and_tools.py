from __future__ import annotations

from datetime import date, datetime
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.models  # noqa: F401 - SQLAlchemy 모델 등록
from app import repositories as repo
from app.db.base import Base
from app.services.chat_tools import ChatToolExecutor
from app.services.chatbot import MealChatAgent
from app.services.kakao_templates import build_kakao_response


class EfooAgentTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.restaurant = repo.get_or_create_restaurant(self.db, "re12", "학생식당")
        self.meal = repo.create_meal(
            self.db,
            self.restaurant.id,
            date(2026, 9, 2),
            "수",
            "중식",
            ["제육볶음", "쌀밥"],
            [],
            "5,000원",
            "",
        )

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_precise_meal_tool_filters_and_tracks_meals(self):
        executor = ChatToolExecutor(
            self.db,
            datetime(2026, 9, 2, 12, 0, tzinfo=ZoneInfo("Asia/Seoul")),
        )
        output = json.loads(
            executor.execute(
                "get_meals",
                {
                    "date": "2026-09-02",
                    "restaurant_codes": ["re12"],
                    "meal_types": ["중식"],
                    "refresh": False,
                },
            )
        )

        self.assertTrue(output["ok"])
        self.assertEqual(output["count"], 1)
        self.assertEqual(output["meals"][0]["menu"], ["제육볶음", "쌀밥"])
        self.assertEqual(executor.selected_meals([self.meal.id]), [self.meal])

    def test_card_without_image_omits_thumbnail(self):
        response = build_kakao_response(
            "오늘 메뉴예요.",
            [self.meal],
            presentation="basic_card",
        )
        card = response["template"]["outputs"][0]["basicCard"]
        self.assertNotIn("thumbnail", card)

    def test_agent_repeats_until_tool_result_then_uses_structured_plan(self):
        user = repo.get_or_create_user(self.db, "agent-test")
        session = repo.get_or_create_active_session(self.db, user)
        first = SimpleNamespace(
            output=[
                SimpleNamespace(
                    type="function_call",
                    name="get_meals",
                    arguments=json.dumps(
                        {
                            "date": "2026-09-02",
                            "restaurant_codes": ["re12"],
                            "meal_types": ["중식"],
                            "refresh": False,
                        }
                    ),
                    call_id="call-1",
                )
            ],
            output_text="",
        )
        second = SimpleNamespace(
            output=[SimpleNamespace(type="message")],
            output_text=json.dumps(
                {
                    "message": "학생식당 점심은 제육볶음과 쌀밥이에요.",
                    "presentation": "basic_card",
                    "meal_ids": [self.meal.id],
                    "meal_intent": True,
                    "quick_replies": [],
                },
                ensure_ascii=False,
            ),
        )
        responses = SimpleNamespace(create=unittest.mock.Mock(side_effect=[first, second]))
        fake_client = SimpleNamespace(responses=responses)
        fake_settings = SimpleNamespace(
            APP_TIMEZONE="Asia/Seoul",
            OPENAI_API_KEY="test-key",
            OPENAI_MODEL="gpt-5.6-luna",
            OPENAI_REASONING_EFFORT="low",
            OPENAI_MAX_TOOL_ROUNDS=8,
            OPENAI_TIMEOUT_SECONDS=15,
            AGENT_TIME_BUDGET_SECONDS=40,
        )

        with patch("app.services.chatbot.OpenAI", return_value=fake_client), patch(
            "app.services.chatbot.settings", fake_settings
        ):
            result = MealChatAgent().run_result(self.db, user, session, "9월 2일 학생식당 점심 알려줘")

        self.assertEqual(responses.create.call_count, 2)
        self.assertEqual(result.presentation, "basic_card")
        self.assertEqual(result.meals, [self.meal])
        self.assertEqual(result.tool_calls[0]["name"], "get_meals")

    def test_legacy_model_does_not_receive_reasoning_options(self):
        user = repo.get_or_create_user(self.db, "legacy-model-test")
        session = repo.get_or_create_active_session(self.db, user)
        response = SimpleNamespace(
            output=[SimpleNamespace(type="message")],
            output_text=json.dumps(
                {
                    "message": "안녕하세요!",
                    "presentation": "simple_text",
                    "meal_ids": [],
                    "meal_intent": False,
                    "quick_replies": [],
                },
                ensure_ascii=False,
            ),
        )
        responses = SimpleNamespace(create=unittest.mock.Mock(return_value=response))
        fake_client = SimpleNamespace(responses=responses)
        fake_settings = SimpleNamespace(
            APP_TIMEZONE="Asia/Seoul",
            OPENAI_API_KEY="test-key",
            OPENAI_MODEL="gpt-4o-mini",
            OPENAI_REASONING_EFFORT="low",
            OPENAI_MAX_TOOL_ROUNDS=4,
            OPENAI_TIMEOUT_SECONDS=15,
            AGENT_TIME_BUDGET_SECONDS=40,
        )

        with patch("app.services.chatbot.OpenAI", return_value=fake_client), patch(
            "app.services.chatbot.settings", fake_settings
        ):
            MealChatAgent().run_result(self.db, user, session, "안녕")

        request = responses.create.call_args.kwargs
        self.assertNotIn("reasoning", request)
        self.assertNotIn("verbosity", request["text"])


if __name__ == "__main__":
    unittest.main()
