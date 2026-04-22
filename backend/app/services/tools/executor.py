"""
Tool executor — sandboxed server-side execution of tenant tools.

Each tool is a registered handler function with:
- Input validation
- Timeout
- No arbitrary code execution
"""
import asyncio
import ipaddress
import logging
import re
import shlex
from dataclasses import dataclass

logger = logging.getLogger(__name__)

TOOL_TIMEOUT_SECONDS = 15
PING_BATCH_TIMEOUT_SECONDS = 60


@dataclass
class ToolResult:
    success: bool
    output: str
    error: str | None = None


# ============================================================
# Registry of built-in tool handlers
# ============================================================
_HANDLERS: dict[str, callable] = {}


def register_tool(name: str):
    """Decorator to register a tool handler."""
    def decorator(func):
        _HANDLERS[name] = func
        return func
    return decorator


def get_available_tools() -> list[str]:
    return list(_HANDLERS.keys())


async def execute_tool(tool_name: str, arguments: dict) -> ToolResult:
    """Execute a registered tool by name with given arguments."""
    handler = _HANDLERS.get(tool_name)
    if not handler:
        return ToolResult(
            success=False,
            output="",
            error=f"Инструмент '{tool_name}' не зарегистрирован. Доступные: {', '.join(_HANDLERS.keys())}",
        )

    # Determine timeout: batch tools get extended timeout
    timeout = TOOL_TIMEOUT_SECONDS
    if tool_name == "ping" and ("ips" in arguments or isinstance(arguments.get("ip"), list)):
        timeout = PING_BATCH_TIMEOUT_SECONDS

    try:
        result = await asyncio.wait_for(handler(arguments), timeout=timeout)
        return result
    except asyncio.TimeoutError:
        return ToolResult(success=False, output="", error=f"Таймаут выполнения ({timeout}с)")
    except Exception as e:
        logger.exception(f"Tool execution error: {tool_name}")
        return ToolResult(success=False, output="", error=f"Ошибка: {str(e)[:300]}")


# ============================================================
# Built-in tools
# ============================================================

def _validate_ip(ip: str) -> str:
    """Validate and sanitize IP address. Raises ValueError if invalid."""
    ip = ip.strip()
    # Allow hostname-like strings too (e.g. google.com) but sanitize
    if re.match(r'^[a-zA-Z0-9.\-:]+$', ip) and len(ip) <= 253:
        # Try to parse as IP first
        try:
            addr = ipaddress.ip_address(ip)
            # Block private/loopback for security
            if addr.is_loopback or addr.is_link_local:
                raise ValueError(f"Адрес {ip} запрещён (loopback/link-local)")
            return str(addr)
        except ValueError:
            # Not an IP, treat as hostname — basic validation
            if re.match(r'^[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]*[a-zA-Z0-9])?)*$', ip):
                return ip
    raise ValueError(f"Некорректный IP/хост: {ip}")


MAX_PING_BATCH = 50  # max IPs per single tool call
PING_CONCURRENCY = 20  # max parallel ping subprocesses


async def _ping_one(target: str) -> str:
    """Ping a single validated target. Returns a one-line result string."""
    cmd = ["ping", "-c", "2", "-W", "3", target]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=TOOL_TIMEOUT_SECONDS)
        output = stdout.decode("utf-8", errors="replace")

        if proc.returncode == 0:
            # Extract rtt line: "rtt min/avg/max/mdev = 0.5/1.2/2.0/0.3 ms"
            for line in output.strip().split("\n"):
                if "min/avg/max" in line:
                    return f"{target}: OK — {line.strip()}"
            return f"{target}: OK"
        else:
            return f"{target}: UNREACHABLE"
    except asyncio.TimeoutError:
        return f"{target}: TIMEOUT"


@register_tool("ping")
async def tool_ping(arguments: dict) -> ToolResult:
    """Ping one or multiple IP addresses/hostnames in parallel.

    Accepts:
      - ip: str  — single address (backward compatible)
      - ips: list[str] — array of addresses for batch parallel ping
    """
    # Support both single "ip" and batch "ips"
    ips_raw: list[str] = []
    if "ips" in arguments and isinstance(arguments["ips"], list):
        ips_raw = arguments["ips"]
    elif "ip" in arguments:
        val = arguments["ip"]
        if isinstance(val, list):
            ips_raw = val
        elif isinstance(val, str):
            ips_raw = [val]

    if not ips_raw:
        return ToolResult(success=False, output="", error="Параметр 'ip' или 'ips' обязателен")

    if len(ips_raw) > MAX_PING_BATCH:
        return ToolResult(
            success=False, output="",
            error=f"Максимум {MAX_PING_BATCH} адресов за один вызов, передано {len(ips_raw)}",
        )

    # Validate all targets first
    targets: list[str] = []
    errors: list[str] = []
    for raw in ips_raw:
        try:
            targets.append(_validate_ip(str(raw)))
        except ValueError as e:
            errors.append(str(e))

    if not targets:
        return ToolResult(success=False, output="", error="; ".join(errors))

    # Ping all targets concurrently with semaphore
    sem = asyncio.Semaphore(PING_CONCURRENCY)

    async def _limited_ping(t: str) -> str:
        async with sem:
            return await _ping_one(t)

    results = await asyncio.gather(*[_limited_ping(t) for t in targets])

    output_lines = list(results)
    if errors:
        output_lines.append(f"\nValidation errors: {'; '.join(errors)}")

    return ToolResult(success=True, output="\n".join(output_lines))


@register_tool("dns_lookup")
async def tool_dns_lookup(arguments: dict) -> ToolResult:
    """Resolve a hostname to IP addresses."""
    host = arguments.get("host", "") or arguments.get("domain", "")
    if not host:
        return ToolResult(success=False, output="", error="Параметр 'host' обязателен")

    host = host.strip()
    if not re.match(r'^[a-zA-Z0-9.\-]+$', host) or len(host) > 253:
        return ToolResult(success=False, output="", error=f"Некорректный хост: {host}")

    try:
        import socket
        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(None, lambda: socket.getaddrinfo(host, None))
        ips = sorted(set(r[4][0] for r in results))
        return ToolResult(success=True, output=f"DNS {host}: {', '.join(ips)}")
    except socket.gaierror:
        return ToolResult(success=False, output="", error=f"Не удалось разрешить {host}")


@register_tool("traceroute")
async def tool_traceroute(arguments: dict) -> ToolResult:
    """Traceroute to a host."""
    ip_raw = arguments.get("ip", "") or arguments.get("host", "")
    if not ip_raw:
        return ToolResult(success=False, output="", error="Параметр 'ip' обязателен")

    try:
        target = _validate_ip(ip_raw)
    except ValueError as e:
        return ToolResult(success=False, output="", error=str(e))

    cmd = ["traceroute", "-m", "15", "-w", "3", target]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        output = stdout.decode("utf-8", errors="replace")
        return ToolResult(success=True, output=output[:2000])
    except asyncio.TimeoutError:
        return ToolResult(success=False, output="", error="Traceroute: таймаут (30с)")
    except FileNotFoundError:
        return ToolResult(success=False, output="", error="traceroute не установлен на сервере")
