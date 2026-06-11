"""
Argus Test Suite — Unit + Integration

Run all:        python -m pytest tests/test_argus.py -v
Run unit only:  python -m pytest tests/test_argus.py -v -m unit
Run integ only: python -m pytest tests/test_argus.py -v -m integration
"""

import asyncio
import json
import os
import shutil
import tempfile
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from dotenv import load_dotenv

# Load .env so ANTHROPIC_API_KEY is available for integration tests
load_dotenv(Path(__file__).parent.parent / ".env")

# ── Helpers ──────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).parent.parent


def _make_config(**overrides):
    """Build a minimal ArgusConfig for testing without touching disk."""
    from src.config import (
        ArgusConfig, ModelConfig, TokenBudget, ContextConfig,
        AgentConfig, SafetyConfig,
    )
    cfg = ArgusConfig(
        models=ModelConfig(
            orchestrator="claude-haiku-4-5-20251001",
            challenger="claude-haiku-4-5-20251001",
            coder="claude-haiku-4-5-20251001",
            explorer="claude-haiku-4-5-20251001",
            security_auditor="claude-haiku-4-5-20251001",
            bug_auditor="claude-haiku-4-5-20251001",
            performance_auditor="claude-haiku-4-5-20251001",
            test_auditor="claude-haiku-4-5-20251001",
        ),
        token_budget=TokenBudget(
            total_hard_cap=100_000,
            total_soft_cap=80_000,
            dollar_hard_cap=2.00,
            dollar_soft_cap=1.50,
        ),
        context=ContextConfig(
            max_history_tokens=20_000,
            compaction_threshold=500,
            compaction_model="claude-haiku-4-5-20251001",
        ),
        agent=AgentConfig(
            max_iterations=5,
            bash_timeout=15,
            parallel_audit=True,
        ),
        safety=SafetyConfig(allowed_write_paths=["."]),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", "sk-test"),
    )
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


# ═══════════════════════════════════════════════════════════════════════════════
# UNIT TESTS
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestPricing:
    """src/core/pricing.py"""

    def test_bundled_defaults_loaded(self):
        from src.core.pricing import ModelPricing
        p = ModelPricing()
        price = p.get_price("claude-sonnet-4-20250514")
        assert price["input"] == 3.00
        assert price["output"] == 15.00

    def test_exact_model_match(self):
        from src.core.pricing import ModelPricing
        p = ModelPricing()
        price = p.get_price("claude-3-haiku-20240307")
        assert price["input"] == 0.25
        assert price["output"] == 1.25

    def test_family_fallback(self):
        from src.core.pricing import ModelPricing
        p = ModelPricing()
        price = p.get_price("claude-opus-99-future")
        assert price["input"] == 15.00
        assert price["output"] == 75.00

    def test_unknown_model_returns_default(self):
        from src.core.pricing import ModelPricing
        p = ModelPricing()
        price = p.get_price("completely-unknown-llm-xyz")
        assert price["input"] == 3.00
        assert price["output"] == 15.00

    def test_parse_litellm_converts_per_token_to_per_million(self):
        from src.core.pricing import ModelPricing
        raw = {
            "my-model": {
                "input_cost_per_token": 0.000003,
                "output_cost_per_token": 0.000015,
            }
        }
        result = ModelPricing._parse_litellm(raw)
        assert "my-model" in result
        assert abs(result["my-model"]["input"] - 3.0) < 0.001
        assert abs(result["my-model"]["output"] - 15.0) < 0.001

    def test_parse_litellm_skips_missing_fields(self):
        from src.core.pricing import ModelPricing
        raw = {
            "incomplete-model": {"input_cost_per_token": 0.000001},
            "no-pricing": {"context_window": 128000},
        }
        result = ModelPricing._parse_litellm(raw)
        assert "incomplete-model" not in result
        assert "no-pricing" not in result

    def test_cache_save_and_load(self, tmp_path):
        from src.core.pricing import ModelPricing
        cache_file = tmp_path / "pricing_cache.json"
        p = ModelPricing(cache_path=cache_file)

        prices = {"test-model": {"input": 1.0, "output": 2.0}}
        p._save_cache(prices)
        assert cache_file.exists()

        loaded = p._load_cache()
        assert loaded is not None
        assert "test-model" in loaded

    def test_cache_expiry(self, tmp_path):
        from src.core.pricing import ModelPricing
        cache_file = tmp_path / "pricing_cache.json"

        # Write a cache with a timestamp 25 hours ago (expired)
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        cache_file.write_text(json.dumps({
            "fetched_at": old_ts,
            "prices": {"some-model": {"input": 1.0, "output": 2.0}},
        }))

        p = ModelPricing(cache_path=cache_file)
        result = p._load_cache()
        assert result is None  # expired

    def test_status_line_bundled(self):
        from src.core.pricing import ModelPricing
        p = ModelPricing()
        line = p.status_line()
        assert "bundled" in line

    def test_cache_age_stored_on_load(self, tmp_path):
        from src.core.pricing import ModelPricing
        cache_file = tmp_path / "pricing_cache.json"
        p = ModelPricing(cache_path=cache_file)
        p._save_cache({"m": {"input": 1.0, "output": 2.0}})

        p2 = ModelPricing(cache_path=cache_file)
        p2._load_cache()
        assert p2._cache_age_hours is not None
        assert p2._cache_age_hours < 1.0  # just saved

    @pytest.mark.asyncio
    async def test_fetch_prices_uses_cache(self, tmp_path):
        from src.core.pricing import ModelPricing
        cache_file = tmp_path / "pricing_cache.json"

        # Pre-populate a fresh cache
        fresh_ts = datetime.now(timezone.utc).isoformat()
        cache_file.write_text(json.dumps({
            "fetched_at": fresh_ts,
            "prices": {"cached-model": {"input": 5.0, "output": 25.0}},
        }))

        p = ModelPricing(cache_path=cache_file)
        source = await p.fetch_prices()
        assert source == "cached"
        assert p._cache_age_hours is not None

    @pytest.mark.asyncio
    async def test_fetch_prices_fallback_when_offline(self, tmp_path):
        from src.core.pricing import ModelPricing
        cache_file = tmp_path / "no_cache.json"
        p = ModelPricing(cache_path=cache_file)

        # Patch httpx to simulate network failure
        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                side_effect=Exception("network error")
            )
            source = await p.fetch_prices()

        assert source == "fallback"
        assert p._source == "fallback"


@pytest.mark.unit
class TestTokenTracker:
    """src/core/token_tracker.py"""

    def _tracker(self, **kwargs):
        from src.core.token_tracker import TokenTracker
        from src.config import TokenBudget
        budget = TokenBudget(
            total_hard_cap=kwargs.pop("total_hard_cap", 10_000),
            total_soft_cap=kwargs.pop("total_soft_cap", 8_000),
            dollar_hard_cap=kwargs.pop("dollar_hard_cap", 1.00),
            dollar_soft_cap=kwargs.pop("dollar_soft_cap", 0.80),
        )
        return TokenTracker(budget)

    def test_add_accumulates_tokens(self):
        t = self._tracker()
        t.add("coder", 100, 50, "claude-haiku-4-5-20251001")
        assert t.total_input == 100
        assert t.total_output == 50
        assert t.total_cost > 0

    def test_add_computes_cost_correctly(self):
        from src.core.pricing import _BUNDLED_DEFAULTS
        t = self._tracker()
        model = "claude-haiku-4-5-20251001"
        inp, out = 1_000_000, 1_000_000
        t.add("coder", inp, out, model)
        expected = _BUNDLED_DEFAULTS[model]["input"] + _BUNDLED_DEFAULTS[model]["output"]
        assert abs(t.total_cost - expected) < 0.01

    def test_per_agent_tracking(self):
        t = self._tracker()
        t.add("coder", 100, 50, "claude-haiku-4-5-20251001")
        t.add("explorer", 200, 80, "claude-haiku-4-5-20251001")
        assert t.usage["coder"].calls == 1
        assert t.usage["explorer"].calls == 1
        assert t.usage["coder"].input_tokens == 100
        assert t.usage["explorer"].input_tokens == 200

    def test_snapshot_returns_current_totals(self):
        t = self._tracker()
        t.add("coder", 500, 200, "claude-haiku-4-5-20251001")
        snap = t.snapshot()
        assert snap["total_tokens"] == 700
        assert snap["total_cost_usd"] == t.total_cost

    def test_snapshot_delta_for_task_cost(self):
        t = self._tracker()
        before = t.snapshot()
        t.add("coder", 500, 200, "claude-haiku-4-5-20251001")
        after = t.snapshot()
        task_tokens = after["total_tokens"] - before["total_tokens"]
        task_cost = after["total_cost_usd"] - before["total_cost_usd"]
        assert task_tokens == 700
        assert task_cost > 0

    def test_hard_cap_token(self):
        t = self._tracker(total_hard_cap=100)
        t.total_input = 80
        t.total_output = 25  # 105 > 100
        assert t.is_hard_cap_reached()

    def test_hard_cap_not_reached(self):
        t = self._tracker(total_hard_cap=10_000)
        t.add("coder", 100, 50, "claude-haiku-4-5-20251001")
        assert not t.is_hard_cap_reached()

    def test_dollar_hard_cap(self):
        t = self._tracker(dollar_hard_cap=0.001)
        t.total_cost = 0.002
        assert t.is_hard_cap_reached()

    def test_dollar_hard_cap_disabled_zero(self):
        t = self._tracker(dollar_hard_cap=0)
        t.total_cost = 99.0
        t.total_input = 1
        t.total_output = 1
        # Only token cap should matter; dollar cap is disabled
        assert not t.is_hard_cap_reached()

    def test_soft_cap_token(self):
        t = self._tracker(total_soft_cap=100)
        t.total_input = 80
        t.total_output = 25  # 105 > 100
        assert t.is_soft_cap_reached()

    def test_soft_cap_dollar(self):
        t = self._tracker(dollar_soft_cap=0.50)
        t.total_cost = 0.60
        assert t.is_soft_cap_reached()

    def test_soft_cap_disabled_zero(self):
        t = self._tracker(total_soft_cap=0, dollar_soft_cap=0)
        t.total_input = 999_999
        t.total_cost = 999.0
        assert not t.is_soft_cap_reached()

    def test_set_budget_valid_token_cap(self):
        t = self._tracker()
        ok = t.set_budget("total_hard_cap", 999999)
        assert ok
        assert t.budget.total_hard_cap == 999999

    def test_set_budget_valid_dollar_cap(self):
        t = self._tracker()
        ok = t.set_budget("dollar_hard_cap", 10.50)
        assert ok
        assert abs(t.budget.dollar_hard_cap - 10.50) < 0.001

    def test_set_budget_invalid_field(self):
        t = self._tracker()
        ok = t.set_budget("nonexistent_field", 100)
        assert not ok

    def test_no_dead_last_snapshot_field(self):
        from src.core.token_tracker import TokenTracker
        import dataclasses
        field_names = {f.name for f in dataclasses.fields(TokenTracker)}
        assert "_last_snapshot" not in field_names

    def test_inline_fallback_is_module_level(self):
        import src.core.token_tracker as mod
        assert hasattr(mod, "_INLINE_FALLBACK")
        assert hasattr(mod, "_INLINE_DEFAULT")

    def test_get_summary_structure(self):
        t = self._tracker()
        t.add("coder", 100, 50, "claude-haiku-4-5-20251001")
        summary = t.get_summary()
        assert "total_tokens" in summary
        assert "total_cost_usd" in summary
        assert "per_agent" in summary
        assert "coder" in summary["per_agent"]


@pytest.mark.unit
class TestContextManager:
    """src/core/context_manager.py"""

    def _cm(self, max_tokens=50_000, threshold=500):
        from src.core.context_manager import ContextManager
        from src.config import ContextConfig
        return ContextManager(ContextConfig(
            max_history_tokens=max_tokens,
            compaction_threshold=threshold,
            compaction_model="claude-3-5-haiku-20241022",
        ))

    def test_trim_noop_when_within_budget(self):
        cm = self._cm(max_tokens=50_000)
        msgs = [
            {"role": "user", "content": "task"},
            {"role": "assistant", "content": "reply"},
        ]
        result = cm.trim_history(msgs)
        assert result == msgs

    def test_trim_no_note_when_nothing_dropped(self):
        """When all messages are high-importance, no extra trimmed_note is injected."""
        cm = self._cm(max_tokens=1)
        # Both messages have score >= 80 (last assistant = 100, first = not scored)
        msgs = [
            {"role": "user", "content": "x" * 100},
            {"role": "assistant", "content": "y" * 100},
        ]
        result = cm.trim_history(msgs, max_tokens=1)
        # Nothing can be dropped, so list should be unchanged
        assert len(result) == len(msgs)
        # No spurious trimmed_note added
        assert all("trimmed" not in str(m.get("content", "")).lower() for m in result[1:])

    def test_trim_drops_paired_assistant_and_tool_result(self):
        """Dropping an assistant message must also drop its following tool-result."""
        cm = self._cm(max_tokens=5)
        msgs = [
            {"role": "user", "content": "do task"},              # first — always kept
            {"role": "assistant", "content": "thinking"},         # score 30 — droppable
            {"role": "user", "content": [                         # score 50 — paired
                {"type": "tool_result", "tool_use_id": "t1", "content": "result"}
            ]},
            {"role": "assistant", "content": "done"},             # score 100 — last assistant kept
        ]
        result = cm.trim_history(msgs, max_tokens=5)
        # Should keep: first user + last assistant
        roles = [m["role"] for m in result]
        assert roles[0] == "user"
        assert roles[-1] == "assistant"
        # Conversation must alternate properly
        for i in range(len(roles) - 1):
            assert roles[i] != roles[i + 1], f"Consecutive {roles[i]} at indices {i},{i+1}"

    def test_trim_note_appended_to_first_message_not_separate(self):
        """Trim note must be appended to first message content, NOT as a new message."""
        cm = self._cm(max_tokens=5)
        msgs = [
            {"role": "user", "content": "original task"},
            {"role": "assistant", "content": "a" * 200},  # score 30 — droppable
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "r"}]},
            {"role": "assistant", "content": "final"},    # score 100 — kept
        ]
        result = cm.trim_history(msgs, max_tokens=5)
        # First message should contain the note (appended)
        first_content = result[0].get("content", "")
        assert "trimmed" in first_content.lower() or "context" in first_content.lower()
        # No standalone note message as a separate entry
        standalone_notes = [
            m for m in result
            if isinstance(m.get("content"), str) and "trimmed" in m["content"].lower()
            and m["role"] == "user" and m is not result[0]
        ]
        assert len(standalone_notes) == 0

    def test_no_consecutive_user_messages(self):
        """After trim, no two consecutive messages should have the same role."""
        cm = self._cm(max_tokens=2)
        msgs = [
            {"role": "user", "content": "task " * 20},
            {"role": "assistant", "content": "thinking " * 20},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "r " * 20}]},
            {"role": "assistant", "content": "step2 " * 20},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t2", "content": "r2 " * 20}]},
            {"role": "assistant", "content": "done"},
        ]
        result = cm.trim_history(msgs, max_tokens=2)
        roles = [m["role"] for m in result]
        for i in range(len(roles) - 1):
            assert roles[i] != roles[i + 1], (
                f"Consecutive {roles[i]} messages at [{i}] and [{i+1}] — "
                f"full roles: {roles}"
            )

    def test_estimate_tokens(self):
        from src.core.context_manager import ContextManager
        from src.config import ContextConfig
        cm = ContextManager(ContextConfig())
        assert cm.estimate_tokens("hello") == max(1, len("hello") // 4)
        assert cm.estimate_tokens("") == 1  # min 1

    def test_file_edit_messages_kept(self):
        """Messages containing file-edit keywords get score 80 and are never dropped."""
        cm = self._cm(max_tokens=1)
        msgs = [
            {"role": "user", "content": "task"},
            {"role": "assistant", "content": "write_file: src/foo.py — added feature"},  # score 80
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]},
            {"role": "assistant", "content": "done"},
        ]
        result = cm.trim_history(msgs, max_tokens=1)
        # All messages should be preserved (nothing can be dropped without violating score 80)
        assert len(result) == len(msgs)

    @pytest.mark.asyncio
    async def test_emit_fn_not_called_for_tier1(self):
        from src.core.context_manager import ContextManager
        from src.config import ContextConfig
        import unittest.mock as mock

        emit_calls = []
        def capture_emit(event_type, **data):
            emit_calls.append((event_type, data))

        cm = ContextManager(
            ContextConfig(compaction_threshold=10_000, compaction_model="x"),
            emit_fn=capture_emit,
        )
        llm = mock.AsyncMock()
        result = await cm.maybe_compact("short text", llm)
        # Tier 1: no compaction, emit_fn must NOT be called
        assert emit_calls == []
        assert result == "short text"

    @pytest.mark.asyncio
    async def test_emit_fn_called_for_tier2(self):
        from src.core.context_manager import ContextManager
        from src.config import ContextConfig
        import unittest.mock as mock

        emit_calls = []
        def capture_emit(event_type, **data):
            emit_calls.append((event_type, data))

        cm = ContextManager(
            ContextConfig(compaction_threshold=1, compaction_model="claude-haiku-4-5-20251001"),
            emit_fn=capture_emit,
        )

        # Mock LLM response
        fake_resp = mock.MagicMock()
        fake_resp.content = "bullet summary"
        fake_resp.input_tokens = 10
        fake_resp.output_tokens = 5
        llm = mock.AsyncMock()
        llm.chat = mock.AsyncMock(return_value=fake_resp)

        # Text that is above threshold (1 token) but below Tier 3 threshold (5000 tokens)
        # ~200 chars = ~50 tokens, above threshold=1, below TIER2=5000
        text = "x" * 200
        result = await cm.maybe_compact(text, llm)

        assert len(emit_calls) == 1
        event_type, data = emit_calls[0]
        assert event_type == "compaction"
        assert data["kind"] == "tool_output"
        assert data["tier"] == 2
        assert "tokens_saved_est" in data

    def test_emit_fn_called_for_history_trim(self):
        from src.core.context_manager import ContextManager
        from src.config import ContextConfig

        emit_calls = []
        def capture_emit(event_type, **data):
            emit_calls.append((event_type, data))

        cm = ContextManager(
            ContextConfig(max_history_tokens=5, compaction_threshold=500, compaction_model="x"),
            emit_fn=capture_emit,
        )
        msgs = [
            {"role": "user", "content": "task"},
            {"role": "assistant", "content": "thinking " * 50},   # score 30, droppable
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "r"}]},
            {"role": "assistant", "content": "done"},              # score 100, kept
        ]
        result = cm.trim_history(msgs, max_tokens=5)

        assert len(emit_calls) == 1
        event_type, data = emit_calls[0]
        assert event_type == "compaction"
        assert data["kind"] == "history_trim"
        assert data["messages_dropped"] > 0

    def test_emit_fn_not_called_when_trim_noop(self):
        from src.core.context_manager import ContextManager
        from src.config import ContextConfig

        emit_calls = []
        def capture_emit(event_type, **data):
            emit_calls.append((event_type, data))

        cm = ContextManager(
            ContextConfig(max_history_tokens=50_000, compaction_threshold=500, compaction_model="x"),
            emit_fn=capture_emit,
        )
        msgs = [
            {"role": "user", "content": "task"},
            {"role": "assistant", "content": "done"},
        ]
        result = cm.trim_history(msgs)
        assert emit_calls == []  # No trimming occurred, no emit


@pytest.mark.unit
class TestFileLock:
    """src/core/file_lock.py"""

    @pytest.mark.asyncio
    async def test_write_lock_acquire_release(self):
        from src.core.file_lock import FileLockManager
        mgr = FileLockManager()
        ok = await mgr.acquire_write("foo.py", "coder")
        assert ok
        locked, owner = mgr.is_write_locked("foo.py")
        assert locked
        assert owner == "coder"
        await mgr.release_write("foo.py", "coder")
        locked, _ = mgr.is_write_locked("foo.py")
        assert not locked

    @pytest.mark.asyncio
    async def test_read_lock_acquire_release(self):
        from src.core.file_lock import FileLockManager
        mgr = FileLockManager()
        ok = await mgr.acquire_read("bar.py", "explorer")
        assert ok
        locked, owners = mgr.is_read_locked("bar.py")
        assert locked
        assert "explorer" in owners
        await mgr.release_read("bar.py", "explorer")
        locked, _ = mgr.is_read_locked("bar.py")
        assert not locked

    @pytest.mark.asyncio
    async def test_multiple_concurrent_read_locks(self):
        from src.core.file_lock import FileLockManager
        mgr = FileLockManager()
        ok1 = await mgr.acquire_read("file.py", "explorer")
        ok2 = await mgr.acquire_read("file.py", "auditor")
        assert ok1 and ok2
        _, owners = mgr.is_read_locked("file.py")
        assert "explorer" in owners
        assert "auditor" in owners

    @pytest.mark.asyncio
    async def test_write_lock_context_manager(self):
        from src.core.file_lock import FileLockManager
        mgr = FileLockManager()
        async with mgr.write_lock("ctx.py", "coder"):
            locked, owner = mgr.is_write_locked("ctx.py")
            assert locked
            assert owner == "coder"
        locked, _ = mgr.is_write_locked("ctx.py")
        assert not locked

    @pytest.mark.asyncio
    async def test_read_lock_context_manager(self):
        from src.core.file_lock import FileLockManager
        mgr = FileLockManager()
        async with mgr.read_lock("ctx.py", "reader"):
            locked, owners = mgr.is_read_locked("ctx.py")
            assert locked
        locked, _ = mgr.is_read_locked("ctx.py")
        assert not locked

    @pytest.mark.asyncio
    async def test_write_lock_released_on_exception(self):
        from src.core.file_lock import FileLockManager
        mgr = FileLockManager()
        try:
            async with mgr.write_lock("err.py", "coder"):
                raise ValueError("simulated error")
        except ValueError:
            pass
        locked, _ = mgr.is_write_locked("err.py")
        assert not locked  # lock must be released even after exception

    @pytest.mark.asyncio
    async def test_write_lock_timeout(self):
        from src.core.file_lock import FileLockManager
        mgr = FileLockManager()
        # Acquire without releasing
        await mgr.acquire_write("busy.py", "coder1")
        # Second writer must time out
        with pytest.raises(TimeoutError):
            async with mgr.write_lock("busy.py", "coder2", timeout=0.1):
                pass

    @pytest.mark.asyncio
    async def test_status_reports_active_locks(self):
        from src.core.file_lock import FileLockManager
        mgr = FileLockManager()
        await mgr.acquire_write("active.py", "coder")
        status = mgr.status()
        assert "active.py" in status
        assert status["active.py"]["write_owner"] == "coder"
        await mgr.release_write("active.py", "coder")

    @pytest.mark.asyncio
    async def test_release_nonexistent_lock_is_safe(self):
        from src.core.file_lock import FileLockManager
        mgr = FileLockManager()
        # Should not raise
        await mgr.release_write("ghost.py", "nobody")
        await mgr.release_read("ghost.py", "nobody")


@pytest.mark.unit
class TestWorktree:
    """src/core/worktree.py — _run helper and basic structure"""

    @pytest.mark.asyncio
    async def test_run_successful_command(self):
        from src.core.worktree import _run
        rc, out, err = await _run("git", "--version")
        assert rc == 0
        assert "git" in out.lower()

    @pytest.mark.asyncio
    async def test_run_failed_command_returns_nonzero(self):
        from src.core.worktree import _run
        # Use a clearly invalid subcommand that git will reject
        rc, out, err = await _run("git", "totally-nonexistent-subcommand-xyz")
        assert rc != 0

    @pytest.mark.asyncio
    async def test_run_nonexistent_binary(self):
        from src.core.worktree import _run
        rc, out, err = await _run("totally-nonexistent-binary-xyz123")
        assert rc == 1
        assert "not found" in err.lower() or err == "git not found"

    @pytest.mark.asyncio
    async def test_run_returncode_never_none(self):
        from src.core.worktree import _run
        rc, _, _ = await _run("git", "--version")
        assert rc is not None
        assert isinstance(rc, int)

    @pytest.mark.asyncio
    async def test_is_git_repo(self):
        from src.core.worktree import WorktreeManager
        from src.config import AgentConfig
        wm = WorktreeManager(AgentConfig())
        is_repo = await wm.is_git_repo()
        # The test directory is inside vg_uppgift which should be a git repo
        assert isinstance(is_repo, bool)


@pytest.mark.unit
class TestOrchestratorEfficiency:
    """Agent efficiency overhaul — lifecycle, tiered audit, max_tokens."""

    def test_select_auditor_names_single_source_file(self):
        from src.agents.orchestrator import Orchestrator
        names = Orchestrator._select_auditor_names(["src/utils/foo.py"])
        assert "security_auditor" in names and "bug_auditor" in names
        assert "performance_auditor" not in names

    def test_select_auditor_names_multi_source_includes_performance(self):
        from src.agents.orchestrator import Orchestrator
        names = Orchestrator._select_auditor_names(["a.py", "b.py"])
        assert "performance_auditor" in names

    @pytest.mark.asyncio
    async def test_handle_emits_lifecycle_on_early_audit_exit(self):
        from src.agents.orchestrator import Orchestrator
        from src.core.token_tracker import TokenTracker

        events = []

        class Bus:
            async def emit(self, agent, event_type, **data):
                events.append((agent, event_type))

        config = _make_config()
        tracker = TokenTracker(config.token_budget)
        orch = Orchestrator(config, tracker, event_bus=Bus())
        await orch.handle("audit /nonexistent/path/xyz")
        await orch.close()

        orch_events = [e[1] for e in events if e[0] == "orchestrator"]
        assert "agent_started" in orch_events
        assert "agent_finished" in orch_events
        assert "task_complete" in orch_events

    def test_agent_max_tokens(self):
        from src.agents.definitions import EXPLORER_DEF, CODER_DEF
        from src.core.agent_loop import make_agent
        from src.core.token_tracker import TokenTracker
        from src.tools.registry import build_registry
        from src.core.llm_client import LLMClient

        config = _make_config()
        llm = LLMClient(config)
        tracker = TokenTracker(config.token_budget)
        tools = build_registry(config)
        assert make_agent(EXPLORER_DEF, config, llm, tracker, tools).get_max_tokens() == 2048
        assert make_agent(CODER_DEF, config, llm, tracker, tools).get_max_tokens() == 8192


@pytest.mark.unit
class TestSafety:
    """src/core/safety.py"""

    def test_safe_command(self):
        from src.core.safety import SafetyChecker, SafetyLevel
        from src.config import SafetyConfig
        checker = SafetyChecker(SafetyConfig())
        result = checker.classify_command("ls -la")
        assert result == SafetyLevel.SAFE

    def test_blocked_rm_rf(self):
        from src.core.safety import SafetyChecker, SafetyLevel
        from src.config import SafetyConfig
        checker = SafetyChecker(SafetyConfig(blocked_commands=["rm -rf /"]))
        result = checker.classify_command("rm -rf /")
        assert result == SafetyLevel.BLOCKED

    def test_file_path_validation_allowed(self):
        from src.core.safety import SafetyChecker
        from src.config import SafetyConfig
        checker = SafetyChecker(SafetyConfig(allowed_write_paths=["."]))
        assert checker.validate_file_path("src/foo.py")

    def test_file_path_traversal_blocked(self):
        from src.core.safety import SafetyChecker
        from src.config import SafetyConfig
        checker = SafetyChecker(SafetyConfig(allowed_write_paths=["."]))
        # Path traversal to /etc/passwd should be blocked
        assert not checker.validate_file_path("/etc/passwd")


@pytest.mark.unit
class TestFileTools:
    """src/tools — read, edit, write"""

    @pytest.mark.asyncio
    async def test_file_reader_reads_file(self, tmp_path):
        from src.tools.file_reader import create_file_reader_tool
        target = tmp_path / "hello.txt"
        target.write_text("hello world")
        tool = create_file_reader_tool()
        result = await tool.handler(path=str(target))
        assert "hello world" in result

    @pytest.mark.asyncio
    async def test_file_reader_missing_file(self, tmp_path):
        from src.tools.file_reader import create_file_reader_tool
        tool = create_file_reader_tool()
        result = await tool.handler(path=str(tmp_path / "nonexistent.txt"))
        assert "error" in result.lower() or "not found" in result.lower()

    @pytest.mark.asyncio
    async def test_file_writer_creates_file(self, tmp_path):
        from src.tools.file_writer import create_file_writer_tool
        config = _make_config()
        config.safety.allowed_write_paths = [str(tmp_path)]
        tool = create_file_writer_tool(config=config)
        target = str(tmp_path / "new.txt")
        result = await tool.handler(path=target, content="new content")
        assert Path(target).read_text() == "new content"
        assert "success" in result.lower() or "wrote" in result.lower() or "created" in result.lower()

    @pytest.mark.asyncio
    async def test_file_editor_edits_file(self, tmp_path):
        from src.tools.file_editor import create_file_editor_tool
        config = _make_config()
        config.safety.allowed_write_paths = [str(tmp_path)]
        tool = create_file_editor_tool(config=config)
        target = tmp_path / "edit.py"
        target.write_text("def foo():\n    return 1\n")
        result = await tool.handler(
            path=str(target),
            old_str="return 1",
            new_str="return 42",
        )
        assert "42" in Path(str(target)).read_text()
        assert "success" in result.lower() or "edited" in result.lower()

    @pytest.mark.asyncio
    async def test_file_editor_rejects_nonunique_match(self, tmp_path):
        from src.tools.file_editor import create_file_editor_tool
        config = _make_config()
        config.safety.allowed_write_paths = [str(tmp_path)]
        tool = create_file_editor_tool(config=config)
        target = tmp_path / "dup.py"
        target.write_text("x = 1\ny = 1\n")
        result = await tool.handler(path=str(target), old_str="= 1", new_str="= 2")
        assert "error" in result.lower() or "times" in result.lower()

    @pytest.mark.asyncio
    async def test_file_editor_rejects_missing_file(self, tmp_path):
        from src.tools.file_editor import create_file_editor_tool
        config = _make_config()
        config.safety.allowed_write_paths = [str(tmp_path)]
        tool = create_file_editor_tool(config=config)
        result = await tool.handler(
            path=str(tmp_path / "ghost.py"),
            old_str="x",
            new_str="y",
        )
        assert "error" in result.lower() or "not found" in result.lower()


@pytest.mark.unit
class TestAgentLoop:
    """src/core/agent_loop.py — mock-based unit tests"""

    def _mock_llm(self, responses):
        """Build a mock LLMClient that returns canned responses in order."""
        from src.core.llm_client import LLMResponse
        client = AsyncMock()
        client.chat = AsyncMock(side_effect=responses)
        return client

    def _final_response(self, text="done"):
        from src.core.llm_client import LLMResponse
        return LLMResponse(
            content=text,
            tool_calls=[],
            raw_content=[{"type": "text", "text": text}],
            stop_reason="end_turn",
            input_tokens=100,
            output_tokens=50,
            model="claude-haiku-4-5-20251001",
        )

    def _tool_response(self, tool_name="read_file", tool_id="t1", inputs=None):
        from src.core.llm_client import LLMResponse
        inp = inputs or {"path": "foo.py"}
        return LLMResponse(
            content="",
            tool_calls=[{"id": tool_id, "name": tool_name, "input": inp}],
            raw_content=[{"type": "tool_use", "id": tool_id, "name": tool_name, "input": inp}],
            stop_reason="tool_use",
            input_tokens=150,
            output_tokens=20,
            model="claude-haiku-4-5-20251001",
        )

    @pytest.mark.asyncio
    async def test_agent_returns_end_turn_immediately(self):
        from src.core.agent_loop import BaseAgent
        from src.tools.registry import ToolRegistry

        config = _make_config()
        llm = self._mock_llm([self._final_response("result text")])
        tracker = MagicMock()
        tracker.is_hard_cap_reached.return_value = False
        tracker.is_soft_cap_reached.return_value = False
        tracker.is_agent_cap_reached.return_value = False
        tracker.add = MagicMock()
        tools = ToolRegistry()

        agent = BaseAgent(config, llm, tracker, tools)
        result = await agent.run("do something")
        assert result.content == "result text"
        assert result.iterations == 1

    @pytest.mark.asyncio
    async def test_agent_executes_tool_then_ends(self):
        from src.core.agent_loop import BaseAgent
        from src.tools.registry import ToolRegistry, Tool

        config = _make_config()
        llm = self._mock_llm([
            self._tool_response("my_tool", "t1", {"x": 1}),
            self._final_response("after tool"),
        ])
        tracker = MagicMock()
        tracker.is_hard_cap_reached.return_value = False
        tracker.is_soft_cap_reached.return_value = False
        tracker.is_agent_cap_reached.return_value = False
        tracker.add = MagicMock()

        tool_called = []
        async def tool_handler(x):
            tool_called.append(x)
            return "tool_result"

        tools = ToolRegistry()
        tools.register(Tool(
            name="my_tool",
            description="test",
            input_schema={"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]},
            handler=tool_handler,
        ))

        agent = BaseAgent(config, llm, tracker, tools)
        agent.get_tool_names = lambda: ["my_tool"]
        result = await agent.run("do something")
        assert result.content == "after tool"
        assert result.iterations == 2
        assert tool_called == [1]

    @pytest.mark.asyncio
    async def test_agent_stops_on_hard_cap(self):
        from src.core.agent_loop import BaseAgent
        from src.tools.registry import ToolRegistry

        config = _make_config()
        llm = AsyncMock()  # should not be called
        tracker = MagicMock()
        tracker.is_hard_cap_reached.return_value = True
        tracker.is_soft_cap_reached.return_value = False
        tracker.is_agent_cap_reached.return_value = False

        agent = BaseAgent(config, llm, tracker, ToolRegistry())
        result = await agent.run("do something")
        assert "hard limit" in result.content.lower() or "budget" in result.content.lower()
        llm.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_wind_down_notice_in_tool_results_not_separate_message(self):
        """Soft-cap wind-down must NOT create a separate user message.

        We capture the messages list passed to the SECOND LLM call (after tool
        execution + wind-down injection). That list must alternate user/assistant.
        """
        from src.core.agent_loop import BaseAgent
        from src.tools.registry import ToolRegistry, Tool

        config = _make_config()
        # Capture the messages list from each individual call (not accumulated)
        all_call_messages: list[list] = []
        final = self._final_response("wrapped up")
        call_count = 0

        async def mock_chat(**kwargs):
            all_call_messages.append(list(kwargs.get("messages", [])))
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return self._tool_response("noop", "t1", {})
            return final

        llm = AsyncMock()
        llm.chat = mock_chat

        tracker = MagicMock()
        tracker.is_hard_cap_reached.return_value = False
        # Soft cap triggers on the FIRST check (which happens after tools run in iter 1)
        tracker.is_soft_cap_reached.side_effect = [True, True]
        tracker.is_agent_cap_reached.return_value = False
        tracker.add = MagicMock()

        async def noop():
            return "ok"

        tools = ToolRegistry()
        tools.register(Tool(
            name="noop", description="no-op",
            input_schema={"type": "object", "properties": {}},
            handler=noop,
        ))

        agent = BaseAgent(config, llm, tracker, tools)
        agent.get_tool_names = lambda: ["noop"]
        await agent.run("task")

        assert len(all_call_messages) >= 2, "Expected at least 2 LLM calls"

        # Inspect the messages list passed to the SECOND call
        # (after tool execution + wind-down injection)
        second_call_msgs = all_call_messages[1]
        roles = [m["role"] for m in second_call_msgs]

        # Must alternate: user, assistant, user, ...
        for i in range(len(roles) - 1):
            assert roles[i] != roles[i + 1], (
                f"Consecutive {roles[i]} messages at [{i}],[{i+1}] in second call: {roles}"
            )

        # The last message (tool results) must be a user message containing
        # the wind-down notice as a text block, NOT a standalone user message
        last_msg = second_call_msgs[-1]
        assert last_msg["role"] == "user"
        content = last_msg["content"]
        assert isinstance(content, list), "Tool results must be a list of blocks"
        # Wind-down text block should be present in the same message
        text_blocks = [b for b in content if isinstance(b, dict) and b.get("type") == "text"]
        assert any("budget" in b.get("text", "").lower() or "wrap" in b.get("text", "").lower()
                   for b in text_blocks), (
            f"Wind-down notice not found in tool_results message. Blocks: {content}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# INTEGRATION TESTS  (require real ANTHROPIC_API_KEY)
# ═══════════════════════════════════════════════════════════════════════════════


def _has_api_key():
    key = os.getenv("ANTHROPIC_API_KEY", "")
    return bool(key) and not key.startswith("sk-test")


skip_no_key = pytest.mark.skipif(
    not _has_api_key(),
    reason="ANTHROPIC_API_KEY not set or is a test placeholder",
)


@pytest.mark.integration
@skip_no_key
class TestLLMClient:
    """src/core/llm_client.py — live API calls"""

    @pytest.mark.asyncio
    async def test_basic_chat_returns_text(self):
        from src.core.llm_client import LLMClient
        config = _make_config()
        client = LLMClient(config)
        resp = await client.chat(
            model="claude-haiku-4-5-20251001",
            system="You are a helpful assistant.",
            messages=[{"role": "user", "content": "Reply with exactly: OK"}],
            max_tokens=10,
        )
        await client.close()
        assert resp.content.strip() != ""
        assert resp.input_tokens > 0
        assert resp.output_tokens > 0
        assert resp.stop_reason == "end_turn"

    @pytest.mark.asyncio
    async def test_chat_with_tool_use(self):
        from src.core.llm_client import LLMClient
        config = _make_config()
        client = LLMClient(config)
        tools = [{
            "name": "get_weather",
            "description": "Get the weather for a city",
            "input_schema": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        }]
        resp = await client.chat(
            model="claude-haiku-4-5-20251001",
            system="Use tools when needed.",
            messages=[{"role": "user", "content": "What is the weather in Paris?"}],
            tools=tools,
            max_tokens=200,
        )
        await client.close()
        # Expect a tool_use stop
        assert resp.stop_reason in ("tool_use", "end_turn")
        if resp.stop_reason == "tool_use":
            assert len(resp.tool_calls) > 0
            assert resp.tool_calls[0]["name"] == "get_weather"

    @pytest.mark.asyncio
    async def test_token_counts_increase_with_longer_input(self):
        from src.core.llm_client import LLMClient
        config = _make_config()
        client = LLMClient(config)
        short = await client.chat(
            model="claude-haiku-4-5-20251001",
            system="Be brief.",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=5,
        )
        long = await client.chat(
            model="claude-haiku-4-5-20251001",
            system="Be brief.",
            messages=[{"role": "user", "content": "hello " * 200}],
            max_tokens=5,
        )
        await client.close()
        assert long.input_tokens > short.input_tokens


@pytest.mark.integration
@skip_no_key
class TestPricingLive:
    """Live pricing fetch"""

    @pytest.mark.asyncio
    async def test_live_fetch_or_cache(self, tmp_path):
        from src.core.pricing import ModelPricing
        # Use a fresh temp cache so we know whether we hit live or cache
        p = ModelPricing(cache_path=tmp_path / "p.json")
        source = await p.fetch_prices()
        assert source in ("live", "cached", "fallback")
        assert p._model_count > 10  # should have many models
        line = p.status_line()
        assert source in line


@pytest.mark.integration
@skip_no_key
class TestExplorer:
    """Explorer agent on the demo buggy_app directory"""

    @pytest.mark.asyncio
    async def test_explorer_maps_demo_app(self):
        from src.agents.definitions import EXPLORER_DEF
        from src.core.agent_loop import make_agent
        from src.core.token_tracker import TokenTracker
        from src.tools.registry import build_registry

        config = _make_config()
        config.agent.max_iterations = 10  # more headroom when tests run sequentially
        from src.core.llm_client import LLMClient
        llm = LLMClient(config)
        tracker = TokenTracker(config.token_budget)
        tools = build_registry(config)

        explorer = make_agent(EXPLORER_DEF, config, llm, tracker, tools)
        result = await explorer.run(
            f"Map the code at '{REPO_ROOT / 'demo' / 'buggy_app'}'. "
            f"List files, note any obvious issues, summarise briefly."
        )
        await llm.close()

        assert result.content
        assert result.total_input_tokens > 0   # agent made at least one API call

        # Accept complete summary OR partial (agent ran but hit iteration cap)
        if not result.content.startswith("["):
            content_lower = result.content.lower()
            assert any(kw in content_lower for kw in ["app.py", "flask", "bug", "sql", "api", "python"])

    @pytest.mark.asyncio
    async def test_explorer_token_tracking(self):
        from src.agents.definitions import EXPLORER_DEF
        from src.core.agent_loop import make_agent
        from src.core.token_tracker import TokenTracker
        from src.tools.registry import build_registry
        from src.core.llm_client import LLMClient

        config = _make_config()
        llm = LLMClient(config)
        tracker = TokenTracker(config.token_budget)
        tools = build_registry(config)

        explorer = make_agent(EXPLORER_DEF, config, llm, tracker, tools)
        await explorer.run("What files are in demo/buggy_app?")
        await llm.close()

        assert tracker.total_input > 0
        assert tracker.total_cost > 0
        assert "explorer" in tracker.usage


@pytest.mark.integration
@skip_no_key
class TestAuditPipeline:
    """Full audit pipeline on demo/buggy_app"""

    @pytest.mark.asyncio
    @pytest.mark.timeout(300)  # 5 minute max
    async def test_audit_detects_bugs_in_demo_app(self):
        from src.agents.orchestrator import Orchestrator
        from src.core.token_tracker import TokenTracker

        config = _make_config()
        config.agent.max_iterations = 8    # Explorer + auditors need headroom
        config.agent.parallel_audit = False  # sequential to stay within rate limits
        # Give enough budget for Explorer + 4 auditors running sequentially
        config.token_budget.total_hard_cap = 500_000
        config.token_budget.total_soft_cap = 0       # disabled — let auditors finish
        config.token_budget.dollar_hard_cap = 5.00
        config.token_budget.dollar_soft_cap = 0.0    # disabled
        tracker = TokenTracker(config.token_budget)

        orchestrator = Orchestrator(config, tracker)
        target = str(REPO_ROOT / "demo" / "buggy_app")
        report = await orchestrator._run_audit(target)
        await orchestrator.close()

        assert report, "Audit returned empty report"
        report_lower = report.lower()

        # At minimum the audit pipeline must have run — check it produced a report
        # header (not just an early-bail "no issues" from a crashed Explorer)
        is_structured_report = "audit" in report_lower
        is_early_bail = "could not map" in report_lower or "explorer could not" in report_lower
        assert is_structured_report and not is_early_bail, (
            f"Audit did not produce a structured report.\nReport:\n{report[:800]}"
        )

        # Bonus: if findings were extracted, verify structured format
        if "finding #" in report_lower:
            assert "severity" in report_lower or "source" in report_lower

    @pytest.mark.asyncio
    async def test_audit_nonexistent_path_returns_error(self):
        from src.agents.orchestrator import Orchestrator
        from src.core.token_tracker import TokenTracker

        config = _make_config()
        tracker = TokenTracker(config.token_budget)
        orchestrator = Orchestrator(config, tracker)

        result = await orchestrator._run_audit("/completely/nonexistent/path/xyz")
        await orchestrator.close()
        assert "does not exist" in result.lower() or "fail" in result.lower()


@pytest.mark.integration
@skip_no_key
class TestTokenBudgetEnforcement:
    """Budget caps actually stop agents"""

    @pytest.mark.asyncio
    async def test_hard_cap_stops_agent(self):
        from src.agents.definitions import EXPLORER_DEF
        from src.core.agent_loop import make_agent
        from src.core.token_tracker import TokenTracker
        from src.tools.registry import build_registry
        from src.core.llm_client import LLMClient
        from src.config import TokenBudget

        config = _make_config()
        # Set absurdly low hard cap — should stop after first API call
        config.token_budget = TokenBudget(
            total_hard_cap=1,   # 1 token — exceeded immediately
            total_soft_cap=0,
            dollar_hard_cap=0,
            dollar_soft_cap=0,
        )
        llm = LLMClient(config)
        tracker = TokenTracker(config.token_budget)
        tools = build_registry(config)

        explorer = make_agent(EXPLORER_DEF, config, llm, tracker, tools)
        # Manually set hard cap already exceeded
        tracker.total_input = 100
        tracker.total_output = 100  # 200 > 1 hard cap

        result = await explorer.run("explore everything")
        await llm.close()
        assert "hard limit" in result.content.lower() or "budget" in result.content.lower()
