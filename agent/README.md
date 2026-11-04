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

The runner writes one `steps.jsonl` row per decision under a unique directory
in `runs/`. Its UTC timestamp is the dispatch time for dispatched actions or
the time the runner recorded a rejection or provider error. A
`not_dispatched` result means the engine received no action for that row.
A provider error without a validated proposal uses a null tool and empty args.
`frame_path` points to the numbered PNG used for the decision, relative to the
run directory. The runner takes a fresh look after a dispatched move or turn.
After pickup, drop, or a rejected proposal it reuses the most recent frame.
Repeated paths record reuse of that observation.

For an integration audit, add `--audit-requests`. The run directory then
contains `claude_requests.jsonl` or `openai_requests.jsonl` with each outbound request body recorded just
before the send attempt. The audit includes the command and camera pixels in
base64. Keep it local and review the bodies before sharing findings. HTTP
headers and API credentials are excluded.

Match audit line N to decision N in `steps.jsonl`, including rejected proposals.
Decode the image and compare it to the referenced PNG. Check that the command,
allowlisted rover state and action history, and four public tools are the only
run context. Keep fixture identity, evaluator results, and native POV out of
requests. A provider request can still consume usage when its action is rejected.

For offline budget planning, maintain a local ledger with one row per run,
including failed runs. Record provider, model, uncached input, cache writes,
cache reads, output tokens, and estimated cost using the approved model rates.
Calculate total estimated spend and flag totals at or above $30. Compare planned
runs with the remaining slots in the 120-run reserve and flag a plan that would
leave fewer slots than needed. Treat estimates and reserve warnings as operator
guidance. The runner does not enforce a monetary cap. Keep cache token classes
separate where the provider reports them, and verify cache use with usage
counters before assuming savings.

Run the client and loop tests with mocked services using
`python -m unittest discover -s agent -v`.
