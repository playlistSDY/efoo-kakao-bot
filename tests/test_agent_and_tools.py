from __future__ import annotations

from datetime import date, datetime, timedelta
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.models  # noqa: F401 - SQLAlchemy 모델 등록
from app import repositories as repo
from app.db.base import Base
from app.domain.meal_images import is_placeholder_meal_image_url, normalize_meal_image_url
from app.models import MealFetchLog
from app.services.chat_tools import ChatToolExecutor
from app.services.chatbot import MealChatAgent, _safe_context_mode
from app.services.kakao_templates import build_kakao_response
from app.services.meals.cache import ensure_fresh_meals
from app.services.meals.image_cache import MealImageCache


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

    def test_stale_meal_cache_returns_immediately_and_schedules_refresh(self):
        now = datetime(2026, 9, 2, 12, 0, tzinfo=ZoneInfo("Asia/Seoul"))
        self.db.add(
            MealFetchLog(
                restaurant_id=self.restaurant.id,
                date=date(2026, 9, 2),
                fetched_at=now - timedelta(hours=4),
                status="success",
            )
        )
        self.db.commit()

        with patch("app.services.meals.cache._schedule_refresh", return_value=True) as schedule:
            status = ensure_fresh_meals(
                self.db,
                date(2026, 9, 2),
                now,
                ["re12"],
                stale_while_revalidate=True,
            )

        self.assertEqual(status["stale_served"], ["re12"])
        self.assertEqual(status["refreshed"], [])
        schedule.assert_called_once_with("re12", date(2026, 9, 2))

    def test_fresh_meal_cache_schedules_non_blocking_image_probe(self):
        now = datetime(2026, 9, 2, 12, 0, tzinfo=ZoneInfo("Asia/Seoul"))
        self.db.add(
            MealFetchLog(
                restaurant_id=self.restaurant.id,
                date=date(2026, 9, 2),
                fetched_at=now,
                status="success",
            )
        )
        self.db.commit()

        with patch("app.services.meals.cache._schedule_image_refresh", return_value=True) as schedule:
            status = ensure_fresh_meals(
                self.db,
                date(2026, 9, 2),
                now,
                ["re12"],
                stale_while_revalidate=True,
            )

        self.assertEqual(status["reused"], ["re12"])
        schedule.assert_called_once_with("re12", date(2026, 9, 2))

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
                    "context_mode": "new",
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
            OPENAI_TOOL_REASONING_EFFORT="none",
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
        requests = responses.create.call_args_list
        self.assertEqual(requests[0].kwargs["reasoning"]["effort"], "none")
        self.assertEqual(requests[1].kwargs["reasoning"]["effort"], "low")

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
                    "context_mode": "new",
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
            OPENAI_TOOL_REASONING_EFFORT="none",
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

    def test_recall_conversation_is_scoped_and_excludes_current_message(self):
        user = repo.get_or_create_user(self.db, "memory-user")
        session = repo.get_or_create_active_session(self.db, user)
        repo.add_message(self.db, session.id, "user", "어제 학생식당 제육 추천해줘")
        repo.add_message(self.db, session.id, "assistant", "제육볶음을 추천할게요")
        current = repo.add_message(self.db, session.id, "user", "전에 추천한 메뉴 뭐였지?")
        other = repo.get_or_create_user(self.db, "other-user")
        other_session = repo.get_or_create_active_session(self.db, other)
        repo.add_message(self.db, other_session.id, "assistant", "비밀 메뉴")
        executor = ChatToolExecutor(
            self.db,
            user_id=user.id,
            excluded_message_ids={current.id},
        )

        output = json.loads(executor.execute("recall_conversation", {"query": "추천 메뉴", "limit": 6}))

        contents = [message["content"] for message in output["messages"]]
        self.assertIn("제육볶음을 추천할게요", contents)
        self.assertNotIn("전에 추천한 메뉴 뭐였지?", contents)
        self.assertNotIn("비밀 메뉴", contents)

    def test_context_mode_distinguishes_new_followup_and_recalled(self):
        self.assertEqual(_safe_context_mode("new", [], "내일 메뉴 알려줘", True), "new")
        self.assertEqual(_safe_context_mode("new", [], "그건 얼마야?", True), "continuation")
        self.assertEqual(
            _safe_context_mode("new", [{"name": "recall_conversation"}], "전에 뭐였지?", True),
            "recalled",
        )

    def test_image_cache_returns_stable_own_domain_url(self):
        with TemporaryDirectory() as directory:
            cache = MealImageCache(directory, "https://bot.example.com/", 1024)
            source = "https://www.hanyang.ac.kr/image/menu.jpg"
            with patch.object(cache, "schedule", return_value=True) as schedule:
                public_url = cache.public_url(source)

            self.assertEqual(public_url, f"https://bot.example.com/media/meals/{cache.key_for(source)}")
            schedule.assert_called_once_with(source)
            self.assertEqual(cache.public_url("https://evil.example/image.jpg"), "https://evil.example/image.jpg")
            self.assertFalse(Path(directory, "unexpected.bin").exists())

    def test_school_no_image_placeholder_is_never_exposed_or_cached(self):
        placeholder = "https://www.hanyang.ac.kr/o/hyu_cafe-web/images/cafe/no-img.png"
        self.assertTrue(is_placeholder_meal_image_url(placeholder))
        self.assertEqual(normalize_meal_image_url(placeholder, "https://www.hanyang.ac.kr"), "")
        with TemporaryDirectory() as directory:
            cache = MealImageCache(directory, "https://bot.example.com", 1024)
            self.assertIsNone(cache.public_url(placeholder))
            self.assertFalse(cache.schedule(placeholder))


if __name__ == "__main__":
    unittest.main()
