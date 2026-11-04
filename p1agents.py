"""Harbor agents with small fixes for Phase 1 runs.

HermesPinned: Harbor's Hermes agent always downloads install.sh from Hermes's main branch, even when a version is
pinned (and checks `hermes version`, which 0.21.4 does not have); main's installer cannot set up the older pinned tag ("No module named 'pm'"). This one runs the installer
from the pinned tag itself. Hermes 0.21.4's session export has token totals but no per-message usage, which Harbor reads, so tokens
would log as 0: they are filled from the session totals instead. Everything else is Harbor's Hermes.

  PYTHONPATH=$PWD harbor run ... -a p1agents:HermesPinned --ak version=v2026.9.21 ...
"""
import json
import os
from pathlib import Path

from harbor.agents.installed.base import BaseInstalledAgent
from harbor.agents.installed.hermes import Hermes
from harbor.models.agent.context import AgentContext
from harbor.environments.base import BaseEnvironment


class HermesPinned(Hermes):
    def get_version_command(self) -> str | None:  # 0.21.4 has `hermes --version`, not `hermes version`
        return 'export PATH="$HOME/.local/bin:$PATH"; hermes --version'

    async def install(self, environment: BaseEnvironment) -> None:
        await self.ensure_system_dependencies(environment, ("curl", "git", "ripgrep", "xz", "build_tools"))  # installer compiles node-pty; it cannot apt as the agent user
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


ZCODE_BUNDLE = Path(__file__).parent / "runs" / "zcode-0.16.9"  # zcode.cjs + zcode-builtin.json from ZCode.app 0.16.9
ZCODE_NODE = "v24.11.0"  # zcode.cjs needs node:sqlite (Node >= 22.13); tarballs cached in ZCODE_BUNDLE
ZCODE_BIN = "/opt/zcode/node/bin/node /opt/zcode/zcode.cjs"


class ZCode(BaseInstalledAgent):
    """ZCode CLI (Z.ai) 0.16.9, bare: memory, skills and plugins off, a fresh home in every container.

    The CLI is one JavaScript file from ZCode.app, run with Node inside the task container. It talks to the model
    through the bridge's /effort-medium/ path, because headless ZCode always asks for reasoning effort "max" and every
    Phase 1 column runs at medium. Tokens come from ZCode's own per-call model log (rollout/model-io-*.jsonl).

      PYTHONPATH=$PWD harbor run ... -a p1agents:ZCode -m anthropic/claude-sonnet-5-5 ...
    """

    DEFAULT_SETUP_TIMEOUT_SEC = 900.0

    @staticmethod
    def name() -> str:
        return "zcode"

    def version(self) -> str | None:
        return "0.16.9"

    def get_version_command(self) -> str | None:
        return f"{ZCODE_BIN} --version"

    async def install(self, environment: BaseEnvironment) -> None:
        # Our own Node, uploaded from the host and called by full path: a task's own `node` may be too old for
        # node:sqlite or Bun's wrapper, and a download from nodejs.org failed in 2 of the first 8 trials.
        await self.ensure_system_dependencies(environment, ("curl", "git", "ripgrep", "xz"))
        r = await self.exec_as_root(environment, command="uname -m")
        arch = "arm64" if (r.stdout or "").strip() in ("aarch64", "arm64") else "x64"
        tar = f"node-{ZCODE_NODE}-linux-{arch}.tar.xz"
        await self.exec_as_root(environment, command="mkdir -p /opt/zcode")
        for f in ("zcode.cjs", "zcode-builtin.json", tar):
            await environment.upload_file(ZCODE_BUNDLE / f, f"/opt/zcode/{f}")
        await self.exec_as_root(environment, timeout_sec=300, command=(
            f"set -e; tar -xJf /opt/zcode/{tar} -C /opt/zcode && rm /opt/zcode/{tar} && "
            f"ln -sfn /opt/zcode/node-{ZCODE_NODE}-linux-{arch} /opt/zcode/node && chmod -R a+rX /opt/zcode && "
            f"{ZCODE_BIN} --version"))

    async def run(self, instruction: str, environment: BaseEnvironment, context: AgentContext) -> None:
        provider, model = (self.model_name or "").split("/", 1)
        config = {"provider": {"ocx": {"kind": "openai-compatible", "name": "opencodex", "options": {
                      "apiKeyRequired": True, "baseURL": os.environ["OPENAI_BASE_URL"].rstrip("/").removesuffix("/v1")
                      + "/effort-medium/v1", "apiKey": os.environ["OPENAI_API_KEY"]},
                      "models": {f"{provider}/{model}": {"name": model}}}},
                  "model": {"main": f"ocx/{provider}/{model}", "lite": f"ocx/{provider}/{model}"},
                  "memory": {"use": False}, "skills": {"enabled": False, "includeInstructions": False},
                  "plugins": {"enabled": False}}
        env = {"HOME": "/tmp/zhome", "HARBOR_INSTRUCTION": instruction,
               "ZCODE_BUILTIN_PROVIDER_CONFIG_FILE": "/opt/zcode/zcode-builtin.json", "ZCODE_CONFIG": json.dumps(config)}
        try:
            await self.exec_as_agent(environment, env=env, command=(
                'mkdir -p "$HOME/.zcode/cli" && printf %s "$ZCODE_CONFIG" > "$HOME/.zcode/cli/config.json" && '
                'chmod 600 "$HOME/.zcode/cli/config.json" && unset ZCODE_CONFIG && '
                f'{ZCODE_BIN} -p "$HARBOR_INSTRUCTION" --cwd "$PWD" --mode yolo 2>&1 '
                "| stdbuf -oL tee /logs/agent/zcode.txt; exit ${PIPESTATUS[0]}"))
        finally:
            await self.exec_as_agent(environment, env={"HOME": "/tmp/zhome"}, timeout_sec=60, command=(
                'cp "$HOME"/.zcode/cli/rollout/*.jsonl /logs/agent/ 2>/dev/null; '
                'cp -r "$HOME"/.zcode/cli/log /logs/agent/zcode-log 2>/dev/null; true'))

    def populate_context_post_run(self, context: AgentContext) -> None:
        n_in = n_cache = n_out = 0
        for f in self.logs_dir.glob("model-io-*.jsonl"):
            for line in f.read_text(errors="replace").splitlines():
                try:
                    u = (json.loads(line).get("response") or {}).get("usage") or {}
                except ValueError:
                    continue
                n_in += u.get("inputTokens") or 0  # includes cached input, as Harbor's n_input_tokens does
                n_cache += u.get("cacheReadTokens") or 0
                n_out += u.get("outputTokens") or 0
        context.n_input_tokens, context.n_cache_tokens, context.n_output_tokens = n_in, n_cache, n_out
