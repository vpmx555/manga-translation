import copy
import json
import tempfile
import unittest
from pathlib import Path

from tests.helpers import make_store
from manga_pipeline.pipeline import DEFAULT_CONFIG
from manga_pipeline.providers.ollama import Ollama
from manga_pipeline.storage.io import atomic_json, fingerprint, read_json


class CacheValidator:
    def __call__(self, value):
        if not isinstance(value.get("valid"), bool):
            raise ValueError("Invalid coverage")

    def validate_cache(self, value):
        if not value["valid"]:
            raise ValueError("Invalid role combination")


class OllamaTests(unittest.TestCase):
    def test_semantically_invalid_live_response_is_returned_but_not_cached(self):
        with tempfile.TemporaryDirectory() as root:
            provider = Ollama(DEFAULT_CONFIG["ollama"], root)
            provider.digest = "test"
            calls = []

            def http(route, body=None):
                calls.append(body)
                return {"done": True, "message": {"content": json.dumps({"valid": len(calls) > 1})}}

            provider.http = http
            check = CacheValidator()
            self.assertEqual(provider.infer("classify_dialogue", "p", {}, {}, check), {"valid": False})
            self.assertFalse(list(Path(root).glob("*.json")))
            self.assertEqual(provider.infer("classify_dialogue", "p", {}, {}, check), {"valid": True})
            self.assertEqual(provider.infer("classify_dialogue", "p", {}, {}, check), {"valid": True})
            self.assertEqual(len(calls), 2)

    def test_old_semantically_invalid_cache_is_ignored_and_replaced(self):
        with tempfile.TemporaryDirectory() as root:
            provider = Ollama(DEFAULT_CONFIG["ollama"], root)
            provider.digest = "test"
            calls = []

            def http(route, body=None):
                calls.append(body)
                return {"done": True, "message": {"content": json.dumps({"valid": True})}}

            provider.http = http
            check = CacheValidator()
            provider.infer("classify_dialogue", "p", {}, {}, check)
            path = next(Path(root).glob("*.json"))
            cached = read_json(path)
            cached["result"] = {"valid": False}
            cached["result_hash"] = fingerprint(cached["result"])
            atomic_json(path, cached)
            self.assertEqual(provider.infer("classify_dialogue", "p", {}, {}, check), {"valid": True})
            self.assertEqual(read_json(path)["result"], {"valid": True})
            self.assertEqual(len(calls), 2)

    def test_no_thinking_and_validated_cache_avoids_second_inference(self):
        with tempfile.TemporaryDirectory() as root:
            provider = Ollama(DEFAULT_CONFIG["ollama"], root)
            requests = []
            def http(route, payload=None):
                if route == "/api/tags":
                    return {"models": [{"name": DEFAULT_CONFIG["ollama"]["model"], "digest": "digest"}]}
                requests.append(payload)
                return {"done": True, "message": {"content": json.dumps({"label": "unknown"})}, "prompt_eval_count": 10}
            provider.http = http
            check = lambda v: self.assertEqual(v, {"label": "unknown"})
            self.assertEqual(provider.infer("classify_mentions", "prompt", {"text": "source"}, {}, check), {"label": "unknown"})
            provider.infer("classify_mentions", "prompt", {"text": "source"}, {}, check)
            self.assertEqual(len(requests), 1)
            self.assertIs(requests[0]["think"], False)
            self.assertNotIn("images", requests[0]["messages"][1])

    def test_truncated_response_is_retried_not_cached(self):
        with tempfile.TemporaryDirectory() as root:
            config = copy.deepcopy(DEFAULT_CONFIG["ollama"])
            config["retries"] = 1
            provider = Ollama(config, root)
            calls = []
            def http(route, payload=None):
                if route == "/api/tags":
                    return {"models": [{"name": config["model"], "digest": "digest"}]}
                calls.append(payload)
                return {"done": True, "done_reason": "length", "message": {"content": "{}"}}
            provider.http = http
            with self.assertRaises(RuntimeError):
                provider.infer("classify_mentions", "prompt", {}, {}, lambda _: None)
            self.assertEqual(len(calls), 2)
