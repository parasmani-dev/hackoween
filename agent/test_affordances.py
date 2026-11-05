"""Both providers expose only currently usable public actions; no HTTP calls."""
import json
import os
import unittest
from unittest.mock import patch

from agent.claude import ClaudeClient, ClaudeError
from agent.openai_client import OpenAIClient, OpenAIError
from agent.tools import tool_definitions


class AffordanceTests(unittest.TestCase):
    @patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'test', 'CLAUDE_MODEL': 'test', 'OPENAI_API_KEY': 'test'})
    def test_both_provider_requests_exclude_failed_pickup_and_restore_it(self):
        for client_type, target in ((ClaudeClient, 'agent.claude.request'),
                                    (OpenAIClient, 'agent.openai_client.request')):
            with self.subTest(provider=client_type.__name__), patch(target) as request:
                if client_type is ClaudeClient:
                    response = {'stop_reason': 'tool_use', 'content': [{'type': 'tool_use', 'name': 'move',
                                'input': {'distance': 0.05}}], 'usage': {'input_tokens': 1, 'output_tokens': 1}}
                else:
                    response = {'choices': [{'finish_reason': 'tool_calls', 'message': {'tool_calls': [
                        {'type': 'function', 'function': {'name': 'move', 'arguments': '{"distance":0.05}'}}]}}],
                        'usage': {'prompt_tokens': 1, 'completion_tokens': 1}}
                request.return_value = ('application/json', json.dumps(response).encode())
                client = client_type()
                client.set_available_actions(['move', 'turn', 'drop'])
                self.assertEqual(client.decide([], system='test').name, 'move')
                offered = request.call_args.kwargs['payload']['tools']
                names = [t['name'] if 'name' in t else t['function']['name'] for t in offered]
                self.assertEqual(names, ['move', 'turn', 'drop'])
                client.set_available_actions(['move', 'turn', 'pickup', 'drop'])
                client.decide([], system='test')
                self.assertEqual(len(request.call_args.kwargs['payload']['tools']), 4)

    @patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'test', 'CLAUDE_MODEL': 'test', 'OPENAI_API_KEY': 'test'})
    def test_provider_cannot_return_an_action_that_was_not_offered(self):
        claude = {'stop_reason': 'tool_use', 'content': [{'type': 'tool_use', 'name': 'pickup', 'input': {}}],
                  'usage': {'input_tokens': 1, 'output_tokens': 1}}
        openai = {'choices': [{'finish_reason': 'tool_calls', 'message': {'tool_calls': [
            {'type': 'function', 'function': {'name': 'pickup', 'arguments': '{}'}}]}}],
            'usage': {'prompt_tokens': 1, 'completion_tokens': 1}}
        for client_type, target, response, error in (
            (ClaudeClient, 'agent.claude.request', claude, ClaudeError),
            (OpenAIClient, 'agent.openai_client.request', openai, OpenAIError)):
            with self.subTest(provider=client_type.__name__), patch(target, return_value=(
                'application/json', json.dumps(response).encode())):
                client = client_type()
                client.set_available_actions(['move', 'turn'])
                with self.assertRaises(error):
                    client.decide([], system='test')

    def test_invalid_affordances_fail_before_provider_request(self):
        for names in ([], ['reset'], ['move', 'reset']):
            with self.assertRaises(ValueError):
                tool_definitions(names)
