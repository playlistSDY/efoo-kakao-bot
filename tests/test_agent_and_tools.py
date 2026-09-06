from __future__ import annotations

from datetime import date, datetime, timedelta
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

import app.models  # noqa: F401 - SQLAlchemy 모델 등록
from app import repositories as repo
from app.db.base import Base
from app.db.init import ensure_user_profile_columns
from app.domain.meal_images import is_placeholder_meal_image_url, normalize_meal_image_url
from app.domain.meal_intent import infer_restaurant_codes, is_fast_meal_lookup
from app.domain.restaurants import meal_service_note
from app.models import MealFetchLog
from app.services.chat_tools import ChatToolExecutor
from app.services.chatbot import MealChatAgent, _fast_meal_answer, _safe_context_mode
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
        self.assertEqual(output["meals"][0]["service_time"], "11:30-13:30")
        self.assertEqual(output["meals"][0]["service_note"], "오늘 중식은 현재 제공 중이며 13:30에 마감해요.")
        self.assertEqual(executor.selected_meals([self.meal.id]), [self.meal])

    def test_agent_memory_tool_persists_and_removes_profile_values(self):
        user = repo.get_or_create_user(self.db, "profile-memory-test")
        executor = ChatToolExecutor(self.db, user_id=user.id)

        preference = json.loads(
            executor.execute(
                "save_user_memory",
                {"category": "preference", "action": "add", "value": "매운 음식"},
            )
        )
        budget = json.loads(
            executor.execute(
                "save_user_memory",
                {"category": "budget", "action": "set", "value": "7천원"},
            )
        )
        note = json.loads(
            executor.execute(
                "save_user_memory",
                {"category": "note", "action": "add", "value": "채식 메뉴를 우선 추천"},
            )
        )
        nickname = json.loads(
            executor.execute(
                "save_user_memory",
                {"category": "nickname", "action": "set", "value": "스디야"},
            )
        )
        speech_style = json.loads(
            executor.execute(
                "save_user_memory",
                {"category": "speech_style", "action": "set", "value": "casual"},
            )
        )
        conversation_preference = json.loads(
            executor.execute(
                "save_user_memory",
                {
                    "category": "conversation_preference",
                    "action": "add",
                    "value": "답변은 짧게",
                },
            )
        )

        self.assertTrue(preference["ok"])
        self.assertEqual(budget["profile"]["budget_limit"], 7000)
        self.assertIn("채식 메뉴를 우선 추천", note["profile"]["notes"])
        self.assertEqual(nickname["profile"]["nickname"], "스디야")
        self.assertEqual(speech_style["profile"]["speech_style"], "casual")
        self.assertEqual(conversation_preference["profile"]["conversation_preferences"], ["답변은 짧게"])
        self.db.refresh(user)
        self.assertEqual(user.preferences, ["매운 음식"])

        removed = json.loads(
            executor.execute(
                "save_user_memory",
                {"category": "preference", "action": "remove", "value": "매운 음식"},
            )
        )
        self.assertEqual(removed["profile"]["preferences"], [])

    def test_existing_user_table_gets_conversation_memory_columns(self):
        legacy_engine = create_engine("sqlite:///:memory:")
        with legacy_engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE user_profiles ("
                    "id INTEGER PRIMARY KEY, kakao_user_id VARCHAR(128) NOT NULL)"
                )
            )

        ensure_user_profile_columns(legacy_engine)

        columns = {column["name"] for column in inspect(legacy_engine).get_columns("user_profiles")}
        self.assertTrue({"nickname", "speech_style", "conversation_preferences"}.issubset(columns))
        legacy_engine.dispose()

    def test_saved_profile_is_always_in_agent_context(self):
        user = repo.get_or_create_user(self.db, "profile-context-test")
        repo.save_user_memory(self.db, user, "allergy", "add", "땅콩")
        repo.save_user_memory(self.db, user, "dislike", "add", "오이")
        repo.save_user_memory(self.db, user, "budget", "set", "8000원")
        repo.save_user_memory(self.db, user, "nickname", "set", "스디야")
        repo.save_user_memory(self.db, user, "speech_style", "set", "casual")
        session = repo.get_or_create_active_session(self.db, user)

        context = MealChatAgent()._build_user_context(
            user,
            "오늘 메뉴 추천해줘",
            datetime(2026, 9, 7, 12, 0, tzinfo=ZoneInfo("Asia/Seoul")),
            repo.get_recent_messages(self.db, session.id),
        )

        self.assertIn("알레르기=['땅콩']", context)
        self.assertIn("비선호=['오이']", context)
        self.assertIn("예산상한=8000", context)
        self.assertIn("호칭=스디야", context)
        self.assertIn("말투=casual", context)

    def test_meal_service_note_uses_target_date_and_current_time(self):
        before_lunch = datetime(2026, 9, 2, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))
        during_lunch = datetime(2026, 9, 2, 12, 0, tzinfo=ZoneInfo("Asia/Seoul"))
        after_lunch = datetime(2026, 9, 2, 14, 0, tzinfo=ZoneInfo("Asia/Seoul"))

        self.assertEqual(
            meal_service_note("re12", "중식", date(2026, 9, 3), before_lunch),
            "내일 중식은 11:30부터 13:30까지 제공해요.",
        )
        self.assertEqual(
            meal_service_note("re12", "중식", date(2026, 9, 2), during_lunch),
            "오늘 중식은 현재 제공 중이며 13:30에 마감해요.",
        )
        self.assertEqual(
            meal_service_note("re12", "중식", date(2026, 9, 2), after_lunch),
            "오늘 중식은 13:30에 마감되었어요.",
        )

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

    def test_missing_meal_cache_schedules_refresh_without_blocking(self):
        now = datetime(2026, 9, 2, 12, 0, tzinfo=ZoneInfo("Asia/Seoul"))
        with patch("app.services.meals.cache._schedule_refresh", return_value=True) as schedule:
            status = ensure_fresh_meals(
                self.db,
                date(2026, 9, 3),
                now,
                ["re12"],
                stale_while_revalidate=True,
                background_if_missing=True,
            )

        self.assertEqual(status["cold_refresh_scheduled"], ["re12"])
        schedule.assert_called_once_with("re12", date(2026, 9, 3))

    def test_fast_lookup_only_handles_independent_menu_queries(self):
        self.assertTrue(is_fast_meal_lookup("오늘 중식 식당별로 정리해줘"))
        self.assertTrue(is_fast_meal_lookup("9월 2일 학생식당 메뉴 알려줘"))
        self.assertFalse(is_fast_meal_lookup("오늘 중식 중에서 하나 추천해줘"))
        self.assertFalse(is_fast_meal_lookup("그럼 학생식당은?"))
        self.assertFalse(is_fast_meal_lookup("오늘이랑 내일 점심 비교해줘"))
        self.assertFalse(is_fast_meal_lookup("나는 매운 음식 좋아해. 오늘 메뉴 알려줘"))
        self.assertFalse(is_fast_meal_lookup("평소 예산은 7000원이야. 오늘 메뉴 알려줘"))
        self.assertFalse(is_fast_meal_lookup("땅콩 알레르기 고려해서 오늘 메뉴 알려줘"))
        self.assertEqual(infer_restaurant_codes("학생식당과 교식 메뉴"), ["re11", "re12"])
        self.assertIsNone(infer_restaurant_codes("오늘 중식 식당별로 정리해줘"))

    def test_fast_meal_answer_uses_readable_restaurant_sections(self):
        second_restaurant = repo.get_or_create_restaurant(self.db, "re11", "교직원식당")
        second_meal = repo.create_meal(
            self.db,
            second_restaurant.id,
            date(2026, 9, 2),
            "수",
            "중식",
            ["불고기", "잡곡밥"],
            [],
            "7,000원",
            "",
        )

        answer = _fast_meal_answer(
            [self.meal, second_meal],
            date(2026, 9, 2),
            datetime(2026, 9, 2, 12, 0, tzinfo=ZoneInfo("Asia/Seoul")),
            False,
        )

        self.assertIn("🍽 9월 2일 메뉴", answer)
        self.assertIn("🏫 학생식당 · 중식", answer)
        self.assertIn("⏰ 오늘 중식은 현재 제공 중이며 13:30에 마감해요.", answer)
        self.assertIn("────────", answer)
        self.assertIn("1. 제육볶음, 쌀밥 (5,000원)", answer)

    def test_fast_meal_answer_references_saved_profile(self):
        user = repo.get_or_create_user(self.db, "fast-profile-test")
        repo.save_user_memory(self.db, user, "allergy", "add", "땅콩")
        repo.save_user_memory(self.db, user, "preference", "add", "매운 음식")
        repo.save_user_memory(self.db, user, "dislike", "add", "오이")
        repo.save_user_memory(self.db, user, "budget", "set", "7000원")
        repo.save_user_memory(self.db, user, "nickname", "set", "스디야")
        repo.save_user_memory(self.db, user, "speech_style", "set", "casual")

        answer = _fast_meal_answer(
            [self.meal],
            date(2026, 9, 2),
            datetime(2026, 9, 2, 12, 0, tzinfo=ZoneInfo("Asia/Seoul")),
            False,
            user,
        )

        self.assertIn("⚠️ 알레르기 기록: 땅콩", answer)
        self.assertIn("🍽 스디야, 9월 2일 메뉴", answer)
        self.assertIn("성분은 식당에 확인해 줘.", answer)
        self.assertIn("지금 제공 중이고 13:30에 마감해.", answer)
        self.assertIn(
            "👤 내 설정 · 선호: 매운 음식 · 비선호: 오이 · 예산: 7,000원 이하 · 호칭: 스디야 · 말투: 반말",
            answer,
        )

    def test_fast_lookup_skips_openai_and_uses_cached_meal(self):
        user = repo.get_or_create_user(self.db, "fast-agent-test")
        session = repo.get_or_create_active_session(self.db, user)
        self.db.add(
            MealFetchLog(
                restaurant_id=self.restaurant.id,
                date=date(2026, 9, 2),
                fetched_at=datetime.now(ZoneInfo("Asia/Seoul")),
                status="success",
            )
        )
        self.db.commit()

        with patch("app.services.chatbot.OpenAI") as openai, patch(
            "app.services.meals.cache._schedule_refresh",
            return_value=True,
        ):
            result = MealChatAgent().run_result(self.db, user, session, "9월 2일 학생식당 중식 알려줘")

        openai.assert_not_called()
        self.assertEqual(result.meals, [self.meal])
        self.assertEqual(result.agent_steps[1], "fast_meal_lookup:get_meals")

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

    @patch(
        "app.services.kakao_templates.public_meal_thumbnail_url",
        return_value="https://chatbot.example.com/media/meals/placeholder.png",
    )
    def test_card_without_image_uses_own_placeholder(self, _thumbnail):
        response = build_kakao_response("오늘 메뉴예요.", [self.meal], presentation="basic_card")
        card = response["template"]["outputs"][0]["basicCard"]
        self.assertEqual(
            card["thumbnail"]["imageUrl"],
            "https://chatbot.example.com/media/meals/placeholder.png",
        )

    @patch("app.services.kakao_templates.public_meal_thumbnail_url", return_value=None)
    def test_card_without_public_image_service_falls_back_to_simple_text(self, _thumbnail):
        response = build_kakao_response("오늘 메뉴예요.", [self.meal], presentation="basic_card")
        self.assertEqual(response["template"]["outputs"][0]["simpleText"]["text"], "오늘 메뉴예요.")

    @patch(
        "app.services.kakao_templates.public_meal_thumbnail_url",
        return_value="https://chatbot.example.com/media/meals/placeholder.png",
    )
    def test_carousel_items_always_include_thumbnail(self, _thumbnail):
        response = build_kakao_response("오늘 메뉴예요.", [self.meal, self.meal], presentation="carousel")
        items = response["template"]["outputs"][0]["carousel"]["items"]
        self.assertTrue(all("thumbnail" in item for item in items))

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
            result = MealChatAgent().run_result(self.db, user, session, "9월 2일 학생식당 점심 중 추천해줘")

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
