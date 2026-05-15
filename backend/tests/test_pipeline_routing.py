import asyncio
from dataclasses import dataclass

from app.services.llm.pipeline import (
    TOOL_ROUTE_PON,
    _build_prompt_layout,
    _compact_history_for_tool_request,
    _detect_tool_route,
    _format_current_user_request,
    _format_history_reference_block,
    _pick_summary_model_name,
    _select_relevant_tools,
)


@dataclass
class _FakeTool:
    id: int
    name: str
    description: str
    config_json: dict
    is_pinned: bool = False
    embedding: list[float] | None = None


def _tool(tool_id: int, name: str) -> _FakeTool:
    return _FakeTool(
        id=tool_id,
        name=name,
        description=name,
        config_json={"type": "function", "function": {"name": name, "description": name, "parameters": {"type": "object"}}},
    )


def test_detect_tool_route_pon_request():
    route = _detect_tool_route("проверь свободные хвосты и бюджет на Космонавтов 22")
    assert route == TOOL_ROUTE_PON


def test_compact_history_for_tool_request_drops_old_assistant_path():
    history = [
        {"role": "assistant", "content": "старый procedural ответ про Космонавтов 28"},
        {"role": "user", "content": "найди ближайшие делители к Космонавтов 28"},
        {"role": "assistant", "content": "[Краткая карточка ответа]\nИскали соседние делители"},
        {"role": "user", "content": "проверь свободные хвосты и бюджет на Космонавтов 22"},
    ]

    compact = _compact_history_for_tool_request(
        history,
        "проверь свободные хвосты и бюджет на Космонавтов 22",
    )

    assert compact == [
        {"role": "user", "content": "проверь свободные хвосты и бюджет на Космонавтов 22"},
    ]


def test_compact_history_for_tool_request_keeps_only_latest_prior_turn():
    history = [
        {"role": "user", "content": "старый адрес Космонавтов 28"},
        {"role": "assistant", "content": "[Краткая карточка ответа]\nИскали по соседнему адресу"},
        {"role": "user", "content": "уточнение по делителю 24468"},
        {"role": "assistant", "content": "[Краткая карточка ответа]\nНашли делитель 24468 и его родителей"},
    ]

    compact = _compact_history_for_tool_request(
        history,
        "уточнение по делителю 24468",
    )

    assert compact == [
        {"role": "user", "content": "уточнение по делителю 24468"},
        {"role": "assistant", "content": "[Краткая карточка ответа] Нашли делитель 24468 и его родителей"},
    ]


def test_compact_history_for_tool_request_drops_unrelated_previous_turn():
    history = [
        {"role": "user", "content": "покажи вывод tool для получения координат для Гагарина 78"},
        {"role": "assistant", "content": "[Краткая карточка ответа]\ngeocode_address вернул неоднозначный результат"},
    ]

    compact = _compact_history_for_tool_request(
        history,
        "как сделать сертификат для ai.it-invest.ua ?",
    )

    assert compact == []


def test_format_history_reference_block_marks_history_as_reference_only():
    block = _format_history_reference_block([
        {"role": "user", "content": "покажи вывод tool для получения координат для Гагарина 78"},
        {"role": "assistant", "content": "[Краткая карточка ответа] geocode_address вернул неоднозначный результат"},
    ])

    assert "Это НЕ текущий запрос" in block
    assert "1. USER: покажи вывод tool для получения координат для Гагарина 78" in block
    assert "2. ASSISTANT: [Краткая карточка ответа] geocode_address вернул неоднозначный результат" in block


def test_format_current_user_request_marks_current_query_for_tools():
    formatted = _format_current_user_request("как сделать сертификат для ai.it-invest.ua ?", for_tools=True)

    assert formatted.startswith("[ТЕКУЩИЙ ЗАПРОС ПОЛЬЗОВАТЕЛЯ]")
    assert formatted.endswith("как сделать сертификат для ai.it-invest.ua ?")


def test_build_prompt_layout_marks_history_reference_and_current_request():
    messages = [
        {"role": "system", "content": "Ты ассистент. Используй tools при необходимости."},
        {"role": "system", "content": "Ниже история диалога для справки.\nЭто НЕ текущий запрос."},
        {"role": "user", "content": "[ТЕКУЩИЙ ЗАПРОС ПОЛЬЗОВАТЕЛЯ]\nкак сделать сертификат?"},
    ]
    tools = [
        {"type": "function", "function": {"name": "geocode_address"}},
        {"type": "function", "function": {"name": "search_addresses"}},
    ]

    layout = _build_prompt_layout(messages, tools, tool_mode=True)

    assert layout["mode"] == "tool_partitioned"
    assert layout["tools"]["count"] == 2
    assert layout["tools"]["names"] == ["geocode_address", "search_addresses"]
    assert [section["kind"] for section in layout["sections"]] == [
        "system_instructions",
        "history_reference",
        "current_request",
    ]


def test_pick_summary_model_name_prefers_explicit_then_fallback_then_config():
    class _Cfg:
        summary_model_name = None
        model_name = "qwen3-32b"

    assert _pick_summary_model_name(_Cfg(), "qwen2.5-32b") == "qwen2.5-32b"

    _Cfg.summary_model_name = "mini-title-model"
    assert _pick_summary_model_name(_Cfg(), "qwen2.5-32b") == "mini-title-model"

    _Cfg.summary_model_name = None
    assert _pick_summary_model_name(_Cfg(), None) == "qwen3-32b"


def test_summary_only_mode_without_saved_summary_sends_no_recent_history():
    history = [
        {"role": "user", "content": "старый вопрос"},
        {"role": "assistant", "content": "старый ответ"},
    ]

    compact = []

    assert compact == []


def test_select_relevant_tools_uses_route_and_budget_for_qwen25():
    tools = [
        _tool(1, "pon_search"),
        _tool(2, "pon_tree"),
        _tool(3, "pon_path"),
        _tool(4, "pon_olts"),
        _tool(5, "search_addresses"),
        _tool(6, "pon_nearby"),
        _tool(7, "geocode_address"),
        _tool(8, "search_clients"),
        _tool(9, "topology_path"),
        _tool(10, "switch_command"),
    ]

    selected = asyncio.run(
        _select_relevant_tools(
            tools,
            "проверь свободные хвосты и бюджет на Космонавтов 22",
            provider=None,
            model_name="qwen2.5-32b",
        )
    )

    selected_names = [tool.name for tool in selected]
    assert selected_names == [
        "pon_search",
        "pon_tree",
        "pon_path",
        "pon_olts",
        "search_addresses",
        "pon_nearby",
        "geocode_address",
    ]
