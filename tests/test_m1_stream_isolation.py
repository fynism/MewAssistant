import asyncio
import json
import unittest
from unittest.mock import patch


class M1StreamIsolationTests(unittest.TestCase):
    def test_failed_stream_does_not_save_incomplete_turn(self):
        from backend import agent

        class FailingAgent:
            async def astream(self, *_args, **_kwargs):
                raise RuntimeError("upstream failed")
                yield  # pragma: no cover

        async def consume():
            return [event async for event in agent.chat_with_agent_stream("question", "user-1", 1)]

        with (patch.object(agent, "create_agent_instance", return_value=FailingAgent()),
              patch.object(agent.storage, "load", return_value=[]),
              patch.object(agent.storage, "save") as save,
              patch.object(agent.storage, "update_title") as update_title,
              patch.object(agent, "generate_session_title") as generate_title,
              patch.object(agent.logger, "exception")):
            events = asyncio.run(consume())

        self.assertTrue(any('"type": "error"' in event for event in events))
        self.assertIn("data: [DONE]\n\n", events)
        save.assert_not_called()
        update_title.assert_not_called()
        generate_title.assert_not_called()

    def test_two_streams_keep_tool_results_and_rag_events_separate(self):
        from langchain_core.messages import AIMessageChunk
        from backend import agent, rag_pipeline
        from backend.rag.events import emit_rag_step

        class FakeAgent:
            def __init__(self, tools):
                self.knowledge = next(tool for tool in tools
                                      if getattr(tool, "name", None) == "search_knowledge_base")

            async def astream(self, *_args, **_kwargs):
                response = await asyncio.to_thread(self.knowledge.invoke, {"query": "same question"})
                await asyncio.sleep(0)
                yield AIMessageChunk(content=response), {}

        def fake_rag(_query, owner_id):
            emit_rag_step("search", f"owner-{owner_id}")
            return {"docs": [{"filename": "same.pdf", "page_number": 1,
                              "text": f"private-{owner_id}"}],
                    "rag_trace": {"owner": owner_id}}

        async def consume(owner_id):
            output = []
            async for event in agent.chat_with_agent_stream("question", f"user-{owner_id}", owner_id):
                output.append(event)
            return output

        async def run_both():
            return await asyncio.gather(consume(1), consume(2))

        with (patch.object(agent, "create_agent", side_effect=lambda model, tools, system_prompt: FakeAgent(tools)),
              patch.object(agent.storage, "load", return_value=[]),
              patch.object(agent.storage, "save"),
              patch.object(agent, "generate_session_title", return_value=""),
              patch.object(rag_pipeline, "run_rag_graph", side_effect=fake_rag)):
            streams = asyncio.run(run_both())

        for owner_id, events in enumerate(streams, 1):
            payloads = [json.loads(item[6:]) for item in events if item.startswith("data: {")]
            content = "".join(item.get("content", "") for item in payloads)
            steps = [item["step"]["label"] for item in payloads if item.get("type") == "rag_step"]
            self.assertIn(f"private-{owner_id}", content)
            self.assertNotIn(f"private-{3 - owner_id}", content)
            self.assertEqual(steps, [f"owner-{owner_id}"])


if __name__ == "__main__":
    unittest.main()
