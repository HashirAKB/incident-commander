"""FakeChatModel: a deterministic stand-in for ChatAnthropic.

It implements the parts of the LangChain chat-model interface the graph uses:
- `bind_tools(tools)` then `ainvoke(messages)` -> an AIMessage with tool calls
  (investigators), or a final text summary once its plan is done;
- `with_structured_output(Schema, include_raw=True)` -> {"raw", "parsed"}
  (triage and synthesis).

Every AIMessage carries `usage_metadata` from a fixed estimator (4 characters
per token), so budgets and costs behave like a live run, deterministically.

Decisions come from fake_rules.py. A small set of recorded responses
(tests/fixtures/model_recordings/<alert_id>/<Schema>.json) overrides the rules
for cases that rules cannot express.

`compromised=True` simulates a hijacked model: it calls write tools it was
never given and follows instructions injected into tool output. Safety tests
use it to prove that the graph, not the model, prevents unapproved writes.
"""

from __future__ import annotations

import json
import math
import os
import re
import time
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable, RunnableLambda
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import BaseModel, Field

from incident_commander.models import fake_rules
from incident_commander.prompts import FINAL_ANSWER_NUDGE

RECORDINGS_DIR = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "model_recordings"
CONTEXT_BLOCK = re.compile(r"```json\n(.*?)\n```", re.DOTALL)


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / 4)


def message_text(message: BaseMessage) -> str:
    """Flatten any message to text (also used for token estimates)."""
    content = message.content
    text = content if isinstance(content, str) else json.dumps(content, sort_keys=True)
    if isinstance(message, AIMessage) and message.tool_calls:
        text += json.dumps([{"name": c["name"], "args": c["args"]} for c in message.tool_calls], sort_keys=True)
    return text


def read_context(messages: list[BaseMessage]) -> dict[str, Any]:
    """Nodes put their inputs in a ```json block in the first human message."""
    for message in messages:
        if isinstance(message, HumanMessage):
            match = CONTEXT_BLOCK.search(message_text(message))
            if match:
                context: dict[str, Any] = json.loads(match.group(1))
                return context
    raise ValueError("FakeChatModel needs a ```json context block in the first human message")


class FakeChatModel(BaseChatModel):
    compromised: bool = False
    recordings_dir: Path = RECORDINGS_DIR
    bound_tools: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "incident-commander-fake"

    # ----------------------------------------------------------- tool calling

    def bind_tools(self, tools: Any, *, tool_choice: str | None = None, **kwargs: Any) -> Runnable[Any, Any]:
        return self.model_copy(update={"bound_tools": [convert_to_openai_tool(t) for t in tools]})

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        context = read_context(messages)
        investigator = context["investigator"]
        _maybe_block(investigator)
        reply = self._investigator_reply(context, messages)
        return ChatResult(generations=[ChatGeneration(message=self._with_usage(reply, messages))])

    def _investigator_reply(self, context: dict[str, Any], messages: list[BaseMessage]) -> AIMessage:
        alert, investigator = context["alert"], context["investigator"]
        plan = fake_rules.investigator_plan(investigator, alert, context["category"])
        if self.compromised:
            plan = [fake_rules.compromised_tool_call(alert), *plan]
        results = [m for m in messages if isinstance(m, ToolMessage)]
        nudged = any(isinstance(m, HumanMessage) and m.content == FINAL_ANSWER_NUDGE for m in messages)
        if len(results) < len(plan) and not nudged:
            call = plan[len(results)]
            call_id = f"call_{investigator}_{len(results)}"
            return AIMessage(content="", tool_calls=[{"name": call["name"], "args": call["args"], "id": call_id}])
        lines: list[str] = []
        for result in results:
            lines += fake_rules.summarize_tool_result(result.name or "", message_text(result))
        return AIMessage(content="\n".join(lines) or "no tool results")

    # ------------------------------------------------------ structured output

    def with_structured_output(self, schema: Any, *, include_raw: bool = False, **kwargs: Any) -> Runnable[Any, Any]:
        assert isinstance(schema, type) and issubclass(schema, BaseModel)

        def run(messages: list[BaseMessage]) -> Any:
            args = self._structured_args(schema.__name__, read_context(messages), messages)
            parsed = schema.model_validate(args)
            raw = AIMessage(
                content="",
                tool_calls=[{"name": schema.__name__, "args": args, "id": f"call_{schema.__name__}"}],
            )
            raw = self._with_usage(raw, messages)
            return {"raw": raw, "parsed": parsed, "parsing_error": None} if include_raw else parsed

        return RunnableLambda(run)

    def _structured_args(self, schema_name: str, context: dict[str, Any], messages: list[BaseMessage]) -> Any:
        recorded = self.recordings_dir / context["alert"]["alert_id"] / f"{schema_name}.json"
        if recorded.exists():
            return json.loads(recorded.read_text(encoding="utf-8"))
        if schema_name == "TriageOutput":
            return fake_rules.triage(context["alert"])
        if schema_name == "SynthesisOutput":
            honest = fake_rules.synthesize(context["alert"], context["triage"]["category"], context["findings"])
            if self.compromised:
                saw = any(fake_rules.contains_injection(message_text(m)) for m in messages)
                return fake_rules.compromised_synthesis(context["alert"], honest, saw)
            return honest
        raise ValueError(f"FakeChatModel has no rule for structured output {schema_name}")

    # ------------------------------------------------------------------ usage

    def _with_usage(self, reply: AIMessage, messages: list[BaseMessage]) -> AIMessage:
        prompt = "".join(message_text(m) for m in messages) + json.dumps(self.bound_tools, sort_keys=True)
        input_tokens, output_tokens = estimate_tokens(prompt), estimate_tokens(message_text(reply))
        reply.usage_metadata = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        }
        return reply


def _maybe_block(investigator: str) -> None:
    """Test hook for crash test (b): hang inside one investigator until killed.

    Active only when IC_FAKE_BLOCK_INVESTIGATOR names this investigator. The
    marker file tells the test process that we are now mid-node.
    """
    if os.environ.get("IC_FAKE_BLOCK_INVESTIGATOR") != investigator:
        return
    marker = os.environ.get("IC_FAKE_BLOCK_MARKER")
    if marker:
        Path(marker).write_text(investigator, encoding="utf-8")
    time.sleep(3600)
