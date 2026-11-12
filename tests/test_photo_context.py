"""Read-only photo context and drawing interoperability, without real APIs or chats."""

import asyncio
from datetime import datetime, timezone
import importlib
from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys
from unittest.mock import AsyncMock, Mock

import pytest

package = ModuleType("mailife_photo_test")
package.__path__ = [str(Path(__file__).resolve().parents[1])]
sys.modules[package.__name__] = package
MaiLifePlugin = importlib.import_module("mailife_photo_test.plugin").MaiLifePlugin


def payload():
    return {"state": {"current_activity": "改预算", "current_location": "公司", "mood_valence": -0.2,
                      "health_note": "private", "user_id": "private"},
            "weather": {"description": "多云", "temperature": 19, "location_name": "城市", "latitude": 1},
            "context": {"current": {"summary": "工作", "location": "办公室"}, "next": {"summary": "private"}}}


def test_snapshot_reads_cached_sources_only_and_exposes_four_fields(monkeypatch):
    monkeypatch.setattr(MaiLifePlugin, "_ready", property(lambda self: True))
    async def run():
        plugin = MaiLifePlugin()
        data = payload()
        plugin._store = SimpleNamespace(get_state=AsyncMock(return_value=data["state"]), get_weather=AsyncMock(return_value=data["weather"]))
        plugin._schedule = SimpleNamespace(context=AsyncMock(return_value=data["context"]))
        plugin._env = SimpleNamespace(now=lambda: datetime.now(timezone.utc), refresh_weather=Mock(side_effect=AssertionError("no network")))
        snapshot = await plugin.api_get_photo_context()
        assert snapshot == {"activity": "改预算", "location": "公司", "weather": "城市 多云，19℃", "mood": "有些闷"}
        plugin._env.refresh_weather.assert_not_called()
        assert snapshot == plugin._photo_context_from_payload(data)
    asyncio.run(run())


@pytest.mark.parametrize("support", [True, False, "unavailable"])
def test_direct_photo_submits_once_and_preserves_legacy_contract(tmp_path, support):
    async def run():
        plugin = MaiLifePlugin()
        (tmp_path / "ref.image").write_bytes(b"fixture-reference")
        plugin._load_persona_refs = lambda: ([{"file": "ref.image"}], str(tmp_path))
        plugin._bot_name = "Yue"
        submitted = []

        async def call(name, **kwargs):
            if name.endswith("drawpic_models"):
                if support == "unavailable":
                    raise RuntimeError("not supported")
                return {"features": {"persona_scene_context": support}}
            assert name == "maimai-drawpic-plugin.drawpic_submit_v2"
            submitted.append(kwargs)
            return {"accepted": True, "task_id": "a" * 12}

        plugin._set_context(SimpleNamespace(api=SimpleNamespace(call=AsyncMock(side_effect=call)), logger=Mock()))
        assert await plugin._submit_persona_ref_draw("stream-a", payload(), "2026-11-12")
        assert len(submitted) == 1
        request = submitted[0]
        assert request["request_id"].startswith("refdraw-") and request["task_type"] == "edit_image"
        if support is True:
            assert request["persona_scene_context"] == {**plugin._photo_context_from_payload(payload()), "source_role": "identity"}
            assert "第一人称" not in request["prompt"] and "改预算" not in request["prompt"]
        else:
            assert "persona_scene_context" not in request
            assert "改预算" in request["prompt"] and "心情有些闷" in request["prompt"]
    asyncio.run(run())


def test_unconfirmed_existing_request_is_queried_without_new_submission(tmp_path):
    async def run():
        plugin = MaiLifePlugin()
        (tmp_path / "ref.image").write_bytes(b"fixture-reference")
        plugin._load_persona_refs = lambda: ([{"file": "ref.image"}], str(tmp_path))
        plugin._bot_name = "Yue"
        plugin._persona_ref_request["stream-a"] = {"day": "2026-11-12", "state": "unconfirmed", "request_id": "original-request"}
        plugin._query_persona_ref_request = AsyncMock(return_value=True)
        calls = AsyncMock(side_effect=AssertionError("must not generate again"))
        plugin._set_context(SimpleNamespace(api=SimpleNamespace(call=calls), logger=Mock()))
        assert not await plugin._submit_persona_ref_draw("stream-a", payload(), "2026-11-12")
        calls.assert_not_awaited()
        plugin._query_persona_ref_request.assert_awaited_once_with("original-request")
    asyncio.run(run())
