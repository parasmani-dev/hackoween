# Rover agent

Install the dependencies with `python -m pip install -r agent/requirements.txt`.
Set `ANTHROPIC_API_KEY` and `CLAUDE_MODEL` in the environment. After the
operator starts an engine run, invoke the agent from the repository root.

```powershell
python -m agent "Fetch the red sphere and drop it in the green zone"
```

The agent supports Claude and OpenAI. For OpenAI, set `OPENAI_API_KEY` and
optionally set `OPENAI_MODEL` to override the default `gpt-5-mini`. The
project key needs write access to `POST /v1/chat/completions` and the project
needs access to the selected model.

```powershell
python -m agent --provider openai "Fetch the red sphere and drop it in the green zone"
```

The CLI prints a JSON result with the stop reason, decision count, token usage,
and run directory. Python callers can use `agent.runner.run_command` directly.
`engine_done` means the engine reached its terminal state. The operator
evaluator reports the task outcome separately. The runner treats `Done` or
`Blocked` with `retry_count >= 3` as exhausted recovery.

The runner writes `steps.jsonl` under a unique directory in `runs/`. Each row
describes one action attempt. Its UTC timestamp is the dispatch time, and
`frame_path` is relative to that run directory and points to the numbered PNG
used for that decision. Consecutive rows may refer to the same frame.

For an integration audit, add `--audit-requests`. The run directory then
contains `claude_requests.jsonl` or `openai_requests.jsonl` with each outbound request body recorded just
before the send attempt. The audit includes the command and camera pixels in
base64. Keep it local and review the bodies before sharing findings. HTTP
headers and API credentials are excluded.

Run the client and loop tests with mocked services using
`python -m unittest discover -s agent -v`.
