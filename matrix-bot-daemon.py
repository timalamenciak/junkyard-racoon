#!/usr/bin/env python3
"""
Matrix Nio Daemon (Modular Command Router)
Listens for messages, routes !commands to external scripts.
Also supports proactive posting of the daily digest via the --post-digest flag.
"""

import asyncio
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from nio import (
    AsyncClient,
    MatrixRoom,
    RoomMessageText,
    LoginResponse,
)

try:
    import yaml
except ImportError:
    yaml = None

# Configuration
HOMESERVER = os.getenv("MATRIX_HOMESERVER", "https://your-matrix-domain.com")
USER_ID = os.getenv("MATRIX_USER_ID", "@bot:your-matrix-domain.com")
PASSWORD = os.getenv("MATRIX_PASSWORD", "")
ROOM_ID = os.getenv("MATRIX_ROOM_ID", "!roomid:your-matrix-domain.com")
COMMANDS_DIR = os.getenv("COMMANDS_DIR", "/home/ubuntu/junkyard-racoon/tools")
SYNC_TIMEOUT = 30000  # 30 seconds
CREDS_FILE = Path(os.getenv("CREDS_FILE", str(Path(__file__).parent / ".credentials.json")))
CONFIG_FILE = Path(os.getenv("MATRIX_BOT_CONFIG", str(Path(__file__).with_name("matrix-bot-daemon.yaml"))))

# Digest file — written by power-tools/output/matrix_digest.py
DIGEST_FILE = Path(os.getenv("MATRIX_DIGEST_FILE", str(Path(__file__).parent / "power-tools/data/output/matrix_digest.txt")))

# Logging — use a local log file by default so the daemon works without root access
_default_log = str(Path(__file__).parent / "matrix-bot-daemon.log")
_log_file = os.getenv("MATRIX_BOT_LOG", _default_log)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(_log_file),
    ],
)
logger = logging.getLogger("matrix-bot-daemon")

client = AsyncClient(HOMESERVER, USER_ID)

DAEMON_CONFIG: dict[str, Any] = {
    "llm": {
        "provider": os.getenv("LLM_PROVIDER", ""),
        "url": os.getenv("LLM_URL", ""),
        "api_key": os.getenv("LLM_API_KEY", ""),
        "model": os.getenv("LLM_MODEL", ""),
        "claude_api_key": os.getenv("ANTHROPIC_API_KEY", ""),
        "claude_model": os.getenv("CLAUDE_MODEL", ""),
    },
    "commands": {},
}


def _deep_update(base: dict[str, Any], updates: dict[str, Any]) -> None:
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_update(base[key], value)
        else:
            base[key] = value


def load_daemon_config() -> None:
    if not CONFIG_FILE.exists():
        logger.info("No daemon YAML config found at %s; using env/defaults", CONFIG_FILE)
        return

    if yaml is None:
        raise RuntimeError(
            "matrix-bot-daemon.yaml found but PyYAML is not installed. Install it with 'pip install pyyaml'."
        )

    with CONFIG_FILE.open("r", encoding="utf-8") as fh:
        loaded = yaml.safe_load(fh) or {}

    if not isinstance(loaded, dict):
        raise ValueError(f"Daemon config must be a YAML mapping: {CONFIG_FILE}")

    _deep_update(DAEMON_CONFIG, loaded)
    logger.info("Loaded daemon config from %s", CONFIG_FILE)


def build_command_env(command: str) -> dict[str, str]:
    env = os.environ.copy()
    llm_cfg = DAEMON_CONFIG.get("llm", {})
    if llm_cfg.get("provider"):
        env["LLM_PROVIDER"] = str(llm_cfg["provider"])
    if llm_cfg.get("url"):
        env["LLM_URL"] = str(llm_cfg["url"])
    if llm_cfg.get("api_key"):
        env["LLM_API_KEY"] = str(llm_cfg["api_key"])
    if llm_cfg.get("model"):
        env["LLM_MODEL"] = str(llm_cfg["model"])
    if llm_cfg.get("claude_api_key"):
        env["ANTHROPIC_API_KEY"] = str(llm_cfg["claude_api_key"])
    if llm_cfg.get("claude_model"):
        env["CLAUDE_MODEL"] = str(llm_cfg["claude_model"])

    command_cfg = DAEMON_CONFIG.get("commands", {}).get(command, {})
    for env_key, cfg_value in command_cfg.get("env", {}).items():
        env[str(env_key)] = str(cfg_value)

    return env


def build_status_message() -> str:
    llm_cfg = DAEMON_CONFIG.get("llm", {})
    provider = llm_cfg.get("provider") or os.getenv("LLM_PROVIDER") or "unset"
    model = (
        llm_cfg.get("model")
        or llm_cfg.get("claude_model")
        or os.getenv("LLM_MODEL")
        or os.getenv("CLAUDE_MODEL")
        or "unset"
    )
    config_source = str(CONFIG_FILE) if CONFIG_FILE.exists() else "env/defaults only"
    commands = ", ".join(get_available_commands()) or "none"
    digest_status = "present" if DIGEST_FILE.exists() else "not found"

    return (
        "Matrix bot status\n"
        f"Config: {config_source}\n"
        f"LLM provider: {provider}\n"
        f"LLM model: {model}\n"
        f"Commands: {commands}\n"
        f"Digest file: {digest_status}"
    )


def build_digest_message() -> str:
    """Return the current daily digest summary for posting to Matrix."""
    if not DIGEST_FILE.exists():
        return "No digest available. Run the nightly pipeline first."
    try:
        return DIGEST_FILE.read_text(encoding="utf-8").strip()
    except Exception as exc:
        return f"Could not read digest: {exc}"


async def run_command_script(command: str, args: str) -> str:
    """
    Execute a command script from the commands directory.
    Scripts should be named: {command}.py
    They receive arguments via stdin and output results to stdout.
    """
    script_path = Path(COMMANDS_DIR) / f"{command}.py"
    command_cfg = DAEMON_CONFIG.get("commands", {}).get(command, {})

    if command_cfg.get("enabled") is False:
        return f"Command !{command} is disabled in daemon config."

    if command == "status":
        return build_status_message()

    if command == "digest":
        return build_digest_message()

    if not script_path.exists():
        return f"Unknown command: !{command}\n\nAvailable commands: {', '.join(get_available_commands())}"

    if not os.access(script_path, os.X_OK):
        logger.warning(f"Script {script_path} is not executable, attempting anyway")

    try:
        result = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, str(script_path)],
            input=args.encode() if args else b"",
            capture_output=True,
            timeout=180,
            text=False,
            env=build_command_env(command),
        )

        if result.returncode == 0:
            output = result.stdout.decode("utf-8", errors="replace").strip()
            return output if output else "Command executed successfully"
        else:
            error = result.stderr.decode("utf-8", errors="replace").strip()
            return f"Command failed:\n{error}"

    except subprocess.TimeoutExpired:
        return f"Command !{command} timed out (limit: 180 seconds)"
    except Exception as e:
        logger.error(f"Error running command {command}: {e}")
        return f"Error: {str(e)}"


def get_available_commands() -> list:
    """List available command scripts."""
    # Built-in commands always available
    built_ins = ["status", "digest"]
    try:
        commands_path = Path(COMMANDS_DIR)
        if not commands_path.exists():
            return built_ins
        available = list(built_ins)
        for script in commands_path.glob("*.py"):
            if script.stem in ("__init__", "status", "digest"):
                continue
            if DAEMON_CONFIG.get("commands", {}).get(script.stem, {}).get("enabled") is False:
                continue
            available.append(script.stem)
        return available
    except Exception as e:
        logger.error(f"Error listing commands: {e}")
        return built_ins


async def message_callback(room: MatrixRoom, event: RoomMessageText) -> None:
    """Handle incoming messages and route commands."""
    if event.sender == USER_ID:
        return

    logger.info(f"Message from {event.sender} in {room.name}: {event.body}")

    if not event.body.startswith("!"):
        return

    parts = event.body[1:].strip().split(maxsplit=1)
    if not parts:
        return

    command = parts[0]
    args = parts[1] if len(parts) > 1 else ""

    logger.info(f"Routing command: {command} with args: {args}")

    await client.room_typing(room.room_id, typing_state=True)

    try:
        response_text = await run_command_script(command, args)
    except Exception as e:
        response_text = f"Error: {str(e)}"
        logger.error(f"Exception in command handler: {e}", exc_info=True)
    finally:
        await client.room_typing(room.room_id, typing_state=False)

    try:
        await client.room_send(
            room_id=room.room_id,
            message_type="m.room.message",
            content={
                "msgtype": "m.text",
                "body": response_text,
            },
        )
        logger.info(f"Sent response to {room.name}")
    except Exception as e:
        logger.error(f"Failed to send message: {e}")


async def post_digest_to_room() -> None:
    """Post the current daily digest to the configured room and exit."""
    message = build_digest_message()
    try:
        await client.room_send(
            room_id=ROOM_ID,
            message_type="m.room.message",
            content={"msgtype": "m.text", "body": message},
        )
        logger.info("Posted digest to room %s", ROOM_ID)
    except Exception as e:
        logger.error("Failed to post digest: %s", e)
        raise


async def sync_loop() -> None:
    """Run the sync loop and handle messages."""
    client.add_event_callback(message_callback, RoomMessageText)

    next_batch_file = CREDS_FILE.with_suffix(".next_batch")
    if next_batch_file.exists():
        client.next_batch = next_batch_file.read_text(encoding="utf-8").strip()
        logger.info("Resuming from saved sync token")

    logger.info("Starting sync loop...")
    try:
        await client.sync_forever(timeout=SYNC_TIMEOUT, full_state=False)
    except Exception as e:
        logger.error(f"Sync loop error: {e}")
    finally:
        if client.next_batch:
            next_batch_file.write_text(client.next_batch, encoding="utf-8")
        await client.close()


async def login() -> None:
    """Login or restore saved session."""
    import json

    if CREDS_FILE.exists():
        logger.info("Loading saved credentials")
        creds = json.loads(CREDS_FILE.read_text(encoding="utf-8"))
        client.restore_login(
            user_id=creds["user_id"],
            device_id=creds["device_id"],
            access_token=creds["access_token"],
        )
        logger.info(f"Restored session for {creds['user_id']}")
    else:
        if not PASSWORD:
            logger.error("MATRIX_PASSWORD environment variable not set")
            sys.exit(1)

        logger.info(f"Logging in as {USER_ID}")
        login_response = await client.login(PASSWORD)

        if isinstance(login_response, LoginResponse):
            logger.info(f"Login successful. Device ID: {login_response.device_id}")
            import json
            CREDS_FILE.write_text(
                json.dumps({
                    "user_id": client.user_id,
                    "device_id": client.device_id,
                    "access_token": client.access_token,
                }),
                encoding="utf-8",
            )
            logger.info(f"Credentials saved to {CREDS_FILE}")
        else:
            logger.error(f"Login failed: {login_response}")
            sys.exit(1)


async def main() -> None:
    """Main entry point."""
    load_daemon_config()
    post_digest_mode = "--post-digest" in sys.argv

    await login()

    try:
        await client.join(ROOM_ID)
        logger.info(f"Joined room {ROOM_ID}")
    except Exception as e:
        logger.warning(f"Could not join room: {e}")

    if post_digest_mode:
        # One-shot: post the digest and exit
        await post_digest_to_room()
        await client.close()
        return

    available = get_available_commands()
    logger.info(f"Available commands: {', '.join(available)}")

    await sync_loop()


if __name__ == "__main__":
    asyncio.run(main())
