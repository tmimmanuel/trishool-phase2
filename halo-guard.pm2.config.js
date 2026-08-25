// pm2 process for the local Halo Guard classify server (scripts/serve_halo_guard.py).
// Started via trishool-lab/run.sh (not via docker-up.sh --local) so it persists
// independently via pm2. Bound to 127.0.0.1 only; OpenClaw in Docker reaches it
// through halo-guard-docker-proxy (socat on docker bridge IPs → 127.0.0.1:8000).
// Local Qwen3Guard is intentionally not started here — output guard uses Chutes.
module.exports = {
  apps: [
    {
      name: "halo-guard-local",
      script: "scripts/serve_halo_guard.py",
      interpreter: "/home/ubuntu/codes/trishool/trishool-phase2/.venv-halo-guard/bin/python",
      args: "--model-path astroware/Halo0.8B-guard-v1 --host 127.0.0.1 --port 8000",
      cwd: "/home/ubuntu/codes/trishool/trishool-phase2",
      env: {
        HALO_GUARD_QUIET_DOWNLOAD: "1",
      },
      autorestart: true,
      max_restarts: 5,
    },
    {
      name: "halo-guard-docker-proxy",
      script: "scripts/halo-guard-docker-proxy.sh",
      interpreter: "bash",
      cwd: "/home/ubuntu/codes/trishool/trishool-phase2",
      autorestart: true,
      max_restarts: 5,
    },
  ],
};
