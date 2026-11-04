# Team coordination

Read your assigned GitHub issue and all comments before starting each phase. The issue is the handoff record. Follow docs/CONTRACT.md. Post a plan, then implement the agreed phases. Keep API keys in environment variables.

## Ownership

- Vernit (@guptavernit2-afk) owns simulation/ and branch feat/vernit-simulation.
- Ayush (@ayushyadav0707) owns engine/ and branch feat/ayush-engine.
- Archit (@karchit1128) owns agent/ and branch feat/archit-agent.
- Paras (@parasmani-dev) owns ui/, integration/, root configuration, docs/, and upstream submodule updates.

Each member creates a branch from main. Do not edit another owner's paths or shared root files without a proposal in the relevant issues and agreement from that owner. Put dependencies in your own directory's requirements.txt. Do not modify the upstream checkout. Preserve attribution for reused code.

After each phase, comment on your issue with completed work, commit SHA, commands and results of verification, pending work, blockers, and requests to another owner. Link dependent issues. Post when blocked immediately. Never include secrets. An agent should run tests on its owned changes and open a draft PR targeting main with the issue linked. Do not merge its own PR. Paras integrates PRs in dependency order and runs end-to-end verification.

Update the issue checklist as phases finish. Check for new comments before further changes. GitHub issues do not automatically wake other agents; each agent must read its issue on startup and between phases. Avoid repeated unchanged status comments.

Separate ownership reduces conflicts but cannot guarantee their absence. Only Paras changes the shared contract after coordinating the affected owners.
