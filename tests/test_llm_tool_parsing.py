from __future__ import annotations

from agent.llm import OllamaClient, TOOLS, _parse_json_text_tool_call


def test_parse_json_text_tool_call_accepts_known_tool():
    content = '''Let's inspect the workspace.\n\n{"name":"list_files","arguments":{"path":"."}}'''

    result = _parse_json_text_tool_call(content, TOOLS)

    assert result is not None
    assert result["function"]["name"] == "list_files"
    assert result["function"]["arguments"] == {"path": "."}


def test_parse_json_text_tool_call_rejects_unknown_tool():
    content = '{"name":"delete_everything","arguments":{}}'

    assert _parse_json_text_tool_call(content, TOOLS) is None


def test_parse_json_text_tool_call_ignores_unrelated_json():
    content = 'Here is some data: {"name":"example","value":123}'

    assert _parse_json_text_tool_call(content, TOOLS) is None


def test_ollama_client_normalizes_text_tool_call():
    class FakeClient:
        def chat(self, **kwargs):
            return {
                "message": {
                    "role": "assistant",
                    "content": 'I should inspect files. {"name":"list_files","arguments":{"path":"."}}',
                    "tool_calls": [],
                }
            }

    client = OllamaClient.__new__(OllamaClient)
    client.host = "http://127.0.0.1:11434"
    client.model = "test-model"
    client._client = FakeClient()

    result = client.chat([{"role": "user", "content": "inspect the workspace"}], tools=TOOLS)

    assert result["content"] == ""
    assert len(result["tool_calls"]) == 1
    assert result["tool_calls"][0]["function"]["name"] == "list_files"
    assert result["tool_calls"][0]["function"]["arguments"] == {"path": "."}


def test_ollama_client_preserves_native_tool_calls():
    class FakeClient:
        def chat(self, **kwargs):
            return {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "native-1",
                            "function": {
                                "name": "list_files",
                                "arguments": {"path": "."},
                            },
                        }
                    ],
                }
            }

    client = OllamaClient.__new__(OllamaClient)
    client.host = "http://127.0.0.1:11434"
    client.model = "test-model"
    client._client = FakeClient()

    result = client.chat([{"role": "user", "content": "inspect the workspace"}], tools=TOOLS)

    assert result["tool_calls"][0]["id"] == "native-1"
    assert result["tool_calls"][0]["function"]["name"] == "list_files"
