"""Unit tests for agent registry."""

import tempfile
from pathlib import Path
from unittest import TestCase, main

from agent_comm.registry import AgentProfile, LocalRegistry


class TestLocalRegistry(TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="registry_test_"))
        self.registry = LocalRegistry(self.tmp / "registry.json")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_register_and_get(self):
        profile = AgentProfile(
            agent_id="agent-a",
            capabilities=["code", "bash"],
            transport="sqlite",
            device="pc1",
        )
        result = self.registry.register(profile)
        self.assertEqual(result.agent_id, "agent-a")

        fetched = self.registry.get("agent-a")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.capabilities, ["code", "bash"])
        self.assertEqual(fetched.device, "pc1")

    def test_get_nonexistent(self):
        self.assertIsNone(self.registry.get("nonexistent"))

    def test_discover_all(self):
        self.registry.register(AgentProfile(agent_id="a1", capabilities=["code"]))
        self.registry.register(AgentProfile(agent_id="a2", capabilities=["research"]))
        agents = self.registry.discover()
        self.assertEqual(len(agents), 2)

    def test_discover_by_capability(self):
        self.registry.register(AgentProfile(agent_id="a1", capabilities=["code", "bash"]))
        self.registry.register(AgentProfile(agent_id="a2", capabilities=["research"]))
        self.registry.register(AgentProfile(agent_id="a3", capabilities=["code"]))

        code_agents = self.registry.discover(capability="code")
        self.assertEqual(len(code_agents), 2)
        ids = {a.agent_id for a in code_agents}
        self.assertEqual(ids, {"a1", "a3"})

    def test_discover_no_match(self):
        self.registry.register(AgentProfile(agent_id="a1", capabilities=["code"]))
        agents = self.registry.discover(capability="nonexistent")
        self.assertEqual(len(agents), 0)

    def test_heartbeat(self):
        self.registry.register(AgentProfile(agent_id="a1", status="inactive"))
        result = self.registry.heartbeat("a1")
        self.assertTrue(result)

        agent = self.registry.get("a1")
        self.assertEqual(agent.status, "active")

    def test_heartbeat_nonexistent(self):
        self.assertFalse(self.registry.heartbeat("nonexistent"))

    def test_deregister(self):
        self.registry.register(AgentProfile(agent_id="a1"))
        self.assertTrue(self.registry.deregister("a1"))
        self.assertIsNone(self.registry.get("a1"))

    def test_deregister_nonexistent(self):
        self.assertFalse(self.registry.deregister("nonexistent"))

    def test_persistence(self):
        self.registry.register(AgentProfile(agent_id="a1", capabilities=["code"]))

        # Create a new registry instance pointing to same file
        registry2 = LocalRegistry(self.tmp / "registry.json")
        agent = registry2.get("a1")
        self.assertIsNotNone(agent)
        self.assertEqual(agent.capabilities, ["code"])

    def test_update_existing(self):
        self.registry.register(AgentProfile(agent_id="a1", capabilities=["code"]))
        self.registry.register(AgentProfile(agent_id="a1", capabilities=["code", "research"]))

        agent = self.registry.get("a1")
        self.assertEqual(agent.capabilities, ["code", "research"])

    def test_minna_calls_generation(self):
        profile = AgentProfile(
            agent_id="claude-code-pc1",
            capabilities=["code", "mcp", "bash"],
            transport="sqlite",
            device="pc1",
        )
        calls = profile.to_minna_calls()

        self.assertEqual(len(calls), 5)
        self.assertEqual(calls[0]["tool"], "memory_add_entity")
        self.assertEqual(calls[0]["args"]["name"], "agent:claude-code-pc1")
        self.assertEqual(calls[0]["args"]["entity_type"], "concept")
        self.assertEqual(calls[1]["tool"], "memory_store")
        self.assertEqual(calls[1]["args"]["value"], "code,mcp,bash")

    def test_list_all(self):
        self.registry.register(AgentProfile(agent_id="a1"))
        self.registry.register(AgentProfile(agent_id="a2"))
        self.registry.register(AgentProfile(agent_id="a3"))
        self.assertEqual(len(self.registry.list_all()), 3)


if __name__ == "__main__":
    main()
