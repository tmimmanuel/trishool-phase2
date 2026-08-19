#!/usr/bin/env python3
"""
Local HTTP server for Qwen3Guard-Stream output moderation.

Default model: Qwen/Qwen3Guard-Stream-0.6B

Endpoints:
  POST /v1/classify  — Halo/OpenClaw-compatible JSON:
      { "query": "<assistant text>", "role": "output", "prompt": "<optional user text>" }
      Returns { status: HARMFUL|HARMLESS, safety_label, category, ... }
  GET  /health

Requires the same venv as serve_halo_guard.py (torch + transformers>=4.51).
trust_remote_code is required for the Stream classification head.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

os.environ.setdefault("PYTHONUNBUFFERED", "1")
if os.environ.get("HF_HUB_DISABLE_XET") is None:
    os.environ["HF_HUB_DISABLE_XET"] = "1"

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO,
    stream=sys.stderr,
)
log = logging.getLogger("serve_qwen3guard_stream")

try:
    import torch
except ImportError as e:  # pragma: no cover
    raise SystemExit("serve_qwen3guard_stream: install PyTorch, e.g. pip install torch") from e

try:
    from transformers import AutoModel, AutoTokenizer
except ImportError as e:  # pragma: no cover
    raise SystemExit("serve_qwen3guard_stream: pip install transformers accelerate") from e


SEVERITY_RANK = {"Safe": 0, "Controversial": 1, "Unsafe": 2}


@dataclass
class ServerState:
    model: Any
    tokenizer: Any
    device: torch.device
    model_id: str
    lock: threading.Lock


def _dtype_for_device(device: torch.device) -> torch.dtype:
    if device.type == "cuda" and torch.cuda.is_bf16_supported():
        return torch.bfloat16
    return torch.float32


def _extract_query(body: dict[str, Any]) -> str | None:
    if isinstance(body.get("query"), str) and body["query"].strip():
        return str(body["query"]).strip()
    messages = body.get("messages")
    if isinstance(messages, list):
        for m in reversed(messages):
            if not isinstance(m, dict):
                continue
            c = m.get("content")
            if isinstance(c, str) and c.strip():
                return c.strip()
    return None


def _user_end_index(tokenizer: Any, token_ids: list[int]) -> int:
    im_start_id = tokenizer.convert_tokens_to_ids("<|im_start|>")
    user_id = tokenizer.convert_tokens_to_ids("user")
    im_end_id = tokenizer.convert_tokens_to_ids("<|im_end|>")
    last_start = next(
        i
        for i in range(len(token_ids) - 1, -1, -1)
        if token_ids[i : i + 2] == [im_start_id, user_id]
    )
    return next(i for i in range(last_start + 2, len(token_ids)) if token_ids[i] == im_end_id)


def _worst_label(levels: list[str]) -> str:
    worst = "Safe"
    for level in levels:
        if SEVERITY_RANK.get(level, 0) > SEVERITY_RANK.get(worst, 0):
            worst = level
    return worst


def _last_category(categories: list[Any]) -> str:
    for item in reversed(categories):
        if isinstance(item, str) and item.strip() and item.strip().lower() not in ("none", "null"):
            return item.strip()
    return "none"


def moderate(state: ServerState, *, prompt: str, response: str, role: str) -> dict[str, Any]:
    tok = state.tokenizer
    user_text = prompt.strip() or "(empty)"
    if role == "input":
        messages = [{"role": "user", "content": response or user_text}]
    else:
        messages = [
            {"role": "user", "content": user_text},
            {"role": "assistant", "content": response},
        ]

    try:
        text = tok.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=False,
            enable_thinking=False,
        )
    except TypeError:
        text = tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=False)

    model_inputs = tok(text, return_tensors="pt")
    token_ids = model_inputs.input_ids[0].to(state.device).long()
    token_ids_list = token_ids.tolist()
    stream_state = None
    result: dict[str, Any] = {"risk_level": ["Safe"], "category": ["None"]}
    collected_levels: list[str] = []
    collected_cats: list[Any] = []
    try:
        if role == "input":
            result, stream_state = state.model.stream_moderate_from_ids(
                token_ids, role="user", stream_state=None
            )
            collected_levels = [str(x) for x in (result.get("risk_level") or [])]
            collected_cats = list(result.get("category") or result.get("category_labels") or [])
        else:
            try:
                user_end = _user_end_index(tok, token_ids_list)
            except StopIteration:
                user_end = -1
            if user_end >= 0:
                result, stream_state = state.model.stream_moderate_from_ids(
                    token_ids[: user_end + 1], role="user", stream_state=None
                )
            for i in range(user_end + 1, token_ids.numel()):
                result, stream_state = state.model.stream_moderate_from_ids(
                    token_ids[i], role="assistant", stream_state=stream_state
                )
                collected_levels.extend(str(x) for x in (result.get("risk_level") or []))
                collected_cats.extend(result.get("category") or result.get("category_labels") or [])
    finally:
        if stream_state is not None:
            try:
                state.model.close_stream(stream_state)
            except Exception:
                pass

    levels = collected_levels or [str(x) for x in (result.get("risk_level") or [])]
    categories = collected_cats or list(result.get("category") or result.get("category_labels") or [])
    safety_label = _worst_label(levels) if levels else "Safe"
    category = _last_category(categories)
    status = "HARMFUL" if safety_label == "Unsafe" else "HARMLESS"
    return {
        "status": status,
        "safety_label": safety_label,
        "category": category,
        "attack_overlay": "none",
        "model": state.model_id,
        "token_count": len(token_ids_list),
        "worst_risk_level": safety_label,
    }


class GuardServer(ThreadingHTTPServer):
    def __init__(self, server_address, handler_class, state: ServerState):
        super().__init__(server_address, handler_class)
        self.state = state


class GuardHandler(BaseHTTPRequestHandler):
    server_version = "Qwen3GuardStream/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        log.info("%s - %s", self.address_string(), fmt % args)

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path.split("?", 1)[0] == "/health":
            st: ServerState = self.server.state  # type: ignore[attr-defined]
            self._send_json(
                200,
                {
                    "status": "ok",
                    "model_type": "qwen3guard_stream",
                    "model_name": st.model_id,
                    "device": str(st.device),
                },
            )
            return
        self._send_json(404, {"error": {"message": "Not found"}})

    def do_POST(self) -> None:
        if self.path.split("?", 1)[0] != "/v1/classify":
            self._send_json(404, {"error": {"message": "Not found"}})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 2_097_152:
                self._send_json(413, {"error": {"message": "Request body too large"}})
                return
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("body must be a JSON object")
        except Exception as exc:
            self._send_json(400, {"error": {"message": f"Invalid JSON: {exc}"}})
            return

        st: ServerState = self.server.state  # type: ignore[attr-defined]
        query = _extract_query(payload)
        if not query:
            self._send_json(400, {"error": {"message": "No query found in request"}})
            return
        raw_role = payload.get("role")
        role = str(raw_role).strip().lower() if isinstance(raw_role, str) else "output"
        if role not in ("input", "output"):
            role = "output"
        prompt = payload.get("prompt") if isinstance(payload.get("prompt"), str) else ""

        log.info("POST /v1/classify role=%s query_chars=%d prompt_chars=%d", role, len(query), len(prompt or ""))
        try:
            with st.lock:
                result = moderate(st, prompt=prompt or "", response=query, role=role)
        except Exception as exc:
            log.exception("classify failed")
            self._send_json(500, {"error": {"message": str(exc)}})
            return
        log.info(
            "POST /v1/classify -> status=%s safety_label=%s category=%s",
            result.get("status"),
            result.get("safety_label"),
            result.get("category"),
        )
        self._send_json(200, result)


def main() -> None:
    parser = argparse.ArgumentParser(description="Local Qwen3Guard-Stream classify server")
    parser.add_argument("--model-path", default="Qwen/Qwen3Guard-Stream-0.6B")
    parser.add_argument("--host", default="127.0.0.1", help="Comma-separated bind hosts")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--local-files-only", action="store_true")
    args = parser.parse_args()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    dtype = _dtype_for_device(device)
    model_id = args.model_path
    log.info("Loading %s on %s dtype=%s", model_id, device, dtype)

    tokenizer = AutoTokenizer.from_pretrained(
        model_id,
        trust_remote_code=True,
        local_files_only=args.local_files_only,
    )
    from transformers import AutoConfig

    config = AutoConfig.from_pretrained(
        model_id,
        trust_remote_code=True,
        local_files_only=args.local_files_only,
    )
    if getattr(config, "pad_token_id", None) is None:
        config.pad_token_id = tokenizer.pad_token_id or tokenizer.eos_token_id or config.eos_token_id
    model = AutoModel.from_pretrained(
        model_id,
        config=config,
        torch_dtype=dtype,
        trust_remote_code=True,
        local_files_only=args.local_files_only,
        low_cpu_mem_usage=True,
    )
    model.to(device)
    model.eval()
    log.info("Model ready")

    state = ServerState(
        model=model,
        tokenizer=tokenizer,
        device=device,
        model_id=model_id,
        lock=threading.Lock(),
    )

    hosts = [h.strip() for h in str(args.host).split(",") if h.strip()]
    servers: list[GuardServer] = []
    for host in hosts:
        try:
            server = GuardServer((host, args.port), GuardHandler, state)
            servers.append(server)
            log.info("READY: Serving on http://%s:%d", host, args.port)
        except OSError as e:
            log.warning("could not bind %s:%d (%s)", host, args.port, e)
    if not servers:
        raise SystemExit(f"serve_qwen3guard_stream: failed to bind any of {hosts} port {args.port}")

    log.info("  POST /v1/classify  - Halo-compatible classify JSON")
    log.info("  GET  /health       - Health check")

    for extra in servers[1:]:
        threading.Thread(target=extra.serve_forever, daemon=True).start()
    servers[0].serve_forever()


if __name__ == "__main__":
    main()
