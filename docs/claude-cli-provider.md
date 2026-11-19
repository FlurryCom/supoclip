# LLM provider `claude-cli` (fork feature)

Runs SupoClip's model requests through the Claude Code CLI signed in with your
Claude subscription, instead of an API key. Each request is one headless
`claude -p` call with no tools, the agent's output schema as `--json-schema`,
and no session saved.

## Set it up

1. Sign in to Claude Code on the host (`claude`, then `/login`) if you haven't.
2. In `.env`, point the worker at that login folder:
   ```env
   LLM=claude-cli:sonnet
   INSTALL_CLAUDE_CLI=true
   CLAUDE_HOME_DIR=/home/you/.claude
   ```
   The part after `claude-cli:` is any `--model` value: `sonnet`, `opus`,
   `haiku`, or a full model ID. `RERANK_LLM` accepts the same form.
3. Rebuild the backend images so they include the CLI:
   ```bash
   docker compose up -d --build backend worker ai-worker
   ```

The worker mounts `CLAUDE_HOME_DIR` at `/root/.claude` (with `:z`, so on
SELinux hosts the folder is relabeled for containers). It then uses the same
login as your Claude Code, and token refreshes are shared. Instead of mounting,
you can set `CLAUDE_CODE_OAUTH_TOKEN` to a token from `claude setup-token`.

## Notes

- Usage counts against your subscription's limits, shared with your own
  Claude Code use. Live clipping (`docs/live-clipping.md`) can send many
  windows; keep its chat filter on.
- `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN` are removed from the CLI's
  environment, so a request never falls back to API billing.
- `CLAUDE_CLI_TIMEOUT_SECONDS` (default 900) bounds one request.
- The AI editor (`AI_EDIT_MODEL`) and social captions still use their own
  providers; this covers clip selection, reranking, workflows and B-roll.
- Code: `backend/src/claude_cli.py`, registered in `backend/src/ai.py`.
