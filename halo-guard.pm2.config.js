// pm2 process for the local Halo Guard classify server (scripts/serve_halo_guard.py).
// Started manually (not via docker-up.sh --local) so it persists independently
// via pm2. Bound to 127.0.0.1 only; OpenClaw in Docker reaches it through
// halo-guard-docker-proxy (socat on docker bridge IPs → 127.0.0.1:8000).
// After a Halo restart, --host can also be comma-separated:
//   127.0.0.1,172.17.0.1,172.18.0.1
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
      name: "qwen3guard-stream-local",
      script: "scripts/serve_qwen3guard_stream.py",
      interpreter: "/home/ubuntu/codes/trishool/trishool-phase2/.venv-qwen3guard/bin/python",
      args: "--model-path Qwen/Qwen3Guard-Stream-0.6B --host 127.0.0.1 --port 8001",
      cwd: "/home/ubuntu/codes/trishool/trishool-phase2",
      env: {
        HF_HUB_DISABLE_XET: "1",
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
    {
      name: "qwen3guard-docker-proxy",
      script: "scripts/halo-guard-docker-proxy.sh",
      interpreter: "bash",
      cwd: "/home/ubuntu/codes/trishool/trishool-phase2",
      env: {
        HALO_GUARD_PORT: "8001",
      },
      autorestart: true,
      max_restarts: 5,
    },
  ],
};
