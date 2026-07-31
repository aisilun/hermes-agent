from types import SimpleNamespace

from agent.conversation_compression import compress_context


class _FakeCompressor:
    def __init__(self):
        self._last_summary_error = "'NoneType' object is not iterable"
        self._last_compress_aborted = True
        self._last_aux_model_failure_model = None
        self._last_aux_model_failure_error = None
        self.compression_count = 1
        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0

    def compress(self, messages, current_tokens=None, focus_topic=None):
        return list(messages)


def test_failed_compression_preserves_history_without_rotation(tmp_path):
    warnings = []
    statuses = []
    agent = SimpleNamespace(
        session_id="sess-1",
        model="gpt-5.5",
        compression_enabled=True,
        _compression_feasibility_checked=True,
        tools=None,
        logs_dir=tmp_path,
        context_compressor=_FakeCompressor(),
        _memory_manager=None,
        _todo_store=SimpleNamespace(format_for_injection=lambda: ""),
        _emit_status=statuses.append,
        _emit_warning=warnings.append,
        _invalidate_system_prompt=lambda: None,
        _build_system_prompt=lambda system_message: f"rebuilt: {system_message}",
        _cached_system_prompt="",
        _session_db=None,
    )
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "keep this exact history"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "latest"},
    ]

    compressed, _ = compress_context(
        agent,
        messages,
        "sys",
        approx_tokens=12345,
    )

    assert compressed == messages
    assert not list((tmp_path / "compression_backups").glob("*"))
    assert "'NoneType' object is not iterable" in warnings[0]
    assert "No messages were dropped" in warnings[0]
