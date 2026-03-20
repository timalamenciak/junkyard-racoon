#!/usr/bin/env python3
"""
Matrix Nio Daemon (Modular Command Router)
Listens for messages, routes !commands to external scripts.
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
CREDS_FILE = os.getenv("CREDS_FILE", "/home/ubuntu/junkyard-racoon/.credentials.json")
CONFIG_FILE = Path(os.getenv("MATRIX_BOT_CONFIG", str(Path(__file__).with_name("matrix-bot-daemon.yaml"))))

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/var/log/matrix-bot-daemon.log"),
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
    "commands": {
        "rss_digest": {
            "enabled": True,
            "env": {},
        }
    },
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

    return (
        "Matrix bot status\n"
        f"Config: {config_source}\n"
        f"LLM provider: {provider}\n"
        f"LLM model: {model}\n"
        f"Commands: {commands}"
    )


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

    if not script_path.exists():
        return f"❌ Unknown command: !{command}\n\nAvailable commands: {', '.join(get_available_commands())}"

    if not os.access(script_path, os.X_OK):
        logger.warning(f"Script {script_path} is not executable, attempting anyway")

    try:
        # Run the script with args as stdin
        result = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, str(script_path)],
            input=args.encode() if args else b"",
            capture_output=True,
            timeout=180,  # 60 second timeout per command
            text=False,
            env=build_command_env(command),
        )

        if result.returncode == 0:
            output = result.stdout.decode("utf-8", errors="replace").strip()
            return output if output else "✓ Command executed successfully"
        else:
            error = result.stderr.decode("utf-8", errors="replace").strip()
            return f"❌ Command failed:\n{error}"

    except subprocess.TimeoutExpired:
        return f"⏱️ Command !{command} timed out (limit: 60 seconds)"
    except Exception as e:
        logger.error(f"Error running command {command}: {e}")
        return f"❌ Error: {str(e)}"


def get_available_commands() -> list:
    """List available command scripts."""
    try:
        commands_path = Path(COMMANDS_DIR)
        if not commands_path.exists():
            return ["status"]
        available = ["status"]
        for script in commands_path.glob("*.py"):
            if script.stem == "__init__" or script.stem == "status":
                continue
            if DAEMON_CONFIG.get("commands", {}).get(script.stem, {}).get("enabled") is False:
                continue
            available.append(script.stem)
        return available
    except Exception as e:
        logger.error(f"Error listing commands: {e}")
        return []


async def message_callback(room: MatrixRoom, event: RoomMessageText) -> None:
    """Handle incoming messages and route commands."""
    # Ignore messages from the bot itself
    if event.sender == USER_ID:
        return

    logger.info(f"Message from {event.sender} in {room.name}: {event.body}")

    # Check for command prefix
    if not event.body.startswith("!"):
        return

    # Parse command and arguments
    parts = event.body[1:].strip().split(maxsplit=1)
    if not parts:
        return

    command = parts[0]
    args = parts[1] if len(parts) > 1 else ""

    logger.info(f"Routing command: {command} with args: {args}")

    # Show typing indicator
    await client.room_typing(room.room_id, typing_state=True)

    try:
        response_text = await run_command_script(command, args)
    except Exception as e:
        response_text = f"Error: {str(e)}"
        logger.error(f"Exception in command handler: {e}", exc_info=True)
    finally:
        await client.room_typing(room.room_id, typing_state=False)

    # Send response
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


async def sync_loop() -> None:
    """Run the sync loop and handle messages."""
    import json

    client.add_event_callback(message_callback, RoomMessageText)

    # Resume from last sync token if available
    next_batch_file = CREDS_FILE.replace(".credentials.json", ".next_batch")
    if os.path.exists(next_batch_file):
        with open(next_batch_file) as f:
            client.next_batch = f.read().strip()
        logger.info("Resuming from saved sync token")

    logger.info("Starting sync loop...")
    try:
        await client.sync_forever(timeout=SYNC_TIMEOUT, full_state=False)
    except Exception as e:
        logger.error(f"Sync loop error: {e}")
    finally:
        # Save sync token before exit
        if client.next_batch:
            with open(next_batch_file, "w") as f:
                f.write(client.next_batch)
        await client.close()

async def main() -> None:
    """Main entry point."""
    import json

    load_daemon_config()

    if os.path.exists(CREDS_FILE):
        logger.info("Loading saved credentials")
        with open(CREDS_FILE) as f:
            creds = json.load(f)
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
            with open(CREDS_FILE, "w") as f:
                json.dump({
                    "user_id": client.user_id,
                    "device_id": client.device_id,
                    "access_token": client.access_token,
                }, f)
            logger.info(f"Credentials saved to {CREDS_FILE}")
        else:
            logger.error(f"Login failed: {login_response}")
            sys.exit(1)

    # Join room if needed
    try:
        await client.join(ROOM_ID)
        logger.info(f"Joined room {ROOM_ID}")
    except Exception as e:
        logger.warning(f"Could not join room: {e}")

    # Log available commands
    available = get_available_commands()
    logger.info(f"Available commands: {', '.join(available)}")

    # Start sync loop
    await sync_loop()

if __name__ == "__main__":
    asyncio.run(main())
