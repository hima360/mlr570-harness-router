"""Harbor agents with small fixes for Phase 1 runs.

HermesPinned: Harbor's Hermes agent always downloads install.sh from Hermes's main branch, even when a version is
pinned (and checks `hermes version`, which 0.21.4 does not have); main's installer cannot set up the older pinned tag ("No module named 'pm'"). This one runs the installer
from the pinned tag itself. Hermes 0.21.4's session export has token totals but no per-message usage, which Harbor reads, so tokens
would log as 0: they are filled from the session totals instead. Everything else is Harbor's Hermes.

  PYTHONPATH=$PWD harbor run ... -a p1agents:HermesPinned --ak version=v2026.9.21 ...
"""
import json

from harbor.agents.installed.hermes import Hermes
from harbor.models.agent.context import AgentContext
from harbor.environments.base import BaseEnvironment


class HermesPinned(Hermes):
    def get_version_command(self) -> str | None:  # 0.21.4 has `hermes --version`, not `hermes version`
        return 'export PATH="$HOME/.local/bin:$PATH"; hermes --version'

    async def install(self, environment: BaseEnvironment) -> None:
        await self.ensure_system_dependencies(environment, ("curl", "git", "ripgrep", "xz"))
        ref = self._version or "main"
        await self.exec_as_agent(
            environment,
            command=(
                "set -euo pipefail; "
                f"curl -fsSL --retry 5 --retry-delay 10 https://raw.githubusercontent.com/NousResearch/hermes-agent/{ref}"
                f"/scripts/install.sh | bash -s -- --skip-setup --branch {ref} && "
                'export PATH="$HOME/.local/bin:$PATH" && '
                'export HERMES_HOME="${HERMES_HOME:-/tmp/hermes}" && '
                'mkdir -p "$HERMES_HOME" "$HERMES_HOME/sessions" "$HERMES_HOME/skills" "$HERMES_HOME/memories" && '
                "hermes --version"
            ),
        )

    def populate_context_post_run(self, context: AgentContext) -> None:
        super().populate_context_post_run(context)
        if context.n_output_tokens:
            return
        path = self.logs_dir / "hermes-session.jsonl"
        if not path.exists():
            return
        s = json.loads(path.read_text().splitlines()[0])
        read, write = s.get("cache_read_tokens") or 0, s.get("cache_write_tokens") or 0
        context.n_input_tokens = (s.get("input_tokens") or 0) + read + write  # Harbor's n_input_tokens includes cache
        context.n_cache_tokens = read
        context.n_output_tokens = s.get("output_tokens") or 0
