"""Provider dispatch — pick Claude or OpenAI by model ID, route to the right
tool-call function with the correct tool set for each pipeline step."""
from menu_ocr.clients_claude import call_claude_tool
from menu_ocr.clients_openai import call_openai_tool
from menu_ocr.schemas import (
    OPENAI_TOOLS_FC,
    OPENAI_TOOLS_MENU,
    TOOLS_FC,
    TOOLS_MENU,
)


def get_provider(model_id: str) -> str:
    """Infer provider from model id prefix."""
    if model_id.startswith("claude-"):
        return "claude"
    if model_id.startswith(("gpt-", "o1", "o3", "o4")):
        return "openai"
    return "unknown"


def _call_tool(client, model, prompt, image_b64_list, max_tokens, cache_prompt,
               *, anthropic_tools, openai_tools, hint):
    if get_provider(model) == "openai":
        return call_openai_tool(client, model, prompt, image_b64_list, max_tokens,
                                tools=openai_tools, hint=hint)
    return call_claude_tool(client, model, prompt, image_b64_list, max_tokens,
                            cache_prompt, tools=anthropic_tools, hint=hint)


def call_fc_scan(client, model, prompt, image_b64_list=None,
                 max_tokens=2048, cache_prompt=True):
    """Step 1: enumerate food_conditions. Returns the standard tool-call dict;
    `tool_name` will be 'submit_food_conditions' or 'report_unreadable'."""
    hint = ("Call `submit_food_conditions` with EVERY unique modifier you see "
            "on the menu. Call `report_unreadable` if the image is too poor.")
    return _call_tool(
        client, model, prompt, image_b64_list, max_tokens, cache_prompt,
        anthropic_tools=TOOLS_FC, openai_tools=OPENAI_TOOLS_FC, hint=hint,
    )


def call_menu_build(client, model, prompt, image_b64_list=None,
                    max_tokens=8192, cache_prompt=True):
    """Step 2: build groups[] using the FC whitelist embedded in `prompt`.
    `tool_name` will be 'submit_menu_groups' or 'report_unreadable'."""
    hint = (
        "Call `submit_menu_groups` to build the menu, OR `report_unreadable` "
        "if the image is too poor. Use only modifier names from the whitelist "
        "in the system prompt."
    )
    return _call_tool(
        client, model, prompt, image_b64_list, max_tokens, cache_prompt,
        anthropic_tools=TOOLS_MENU, openai_tools=OPENAI_TOOLS_MENU, hint=hint,
    )
