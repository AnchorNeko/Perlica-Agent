"""Slash command service layer used by interactive chat frontends."""

from __future__ import annotations

import json
import shlex
import sys
from dataclasses import dataclass, field
from io import StringIO
from typing import Callable, Dict, Iterable, List, Optional, TextIO, Tuple

from perlica.config import ALLOWED_PROVIDERS, load_settings
from perlica.kernel.context_ops import clear_session_context
from perlica.kernel.runtime import Runtime
from perlica.kernel.session_store import SessionRecord, SessionStore
from perlica.ui.render import (
    render_doctor_text,
    render_notice,
    render_repl_help_summary,
)


@dataclass
class ReplState:
    context_id: str
    provider: Optional[str]
    yes: bool
    session_ref: Optional[str]
    session_name: Optional[str] = None
    session_is_ephemeral: bool = False
    service_hooks: Optional["ServiceCommandHooks"] = None
    interaction_hooks: Optional["InteractionCommandHooks"] = None


@dataclass
class InteractionCommandHooks:
    """Interaction hooks shared by TUI and service modes."""

    pending: Callable[[], str]
    choose: Callable[[str, str], str]
    has_pending: Optional[Callable[[], bool]] = None
    choice_suggestions: Optional[Callable[[], List[str]]] = None


@dataclass
class ServiceCommandHooks:
    """Service-mode hooks injected by bridge controller/orchestrator."""

    status: Callable[[], str]
    rebind: Callable[[], str]
    unpair: Callable[[], str]
    channel_list: Optional[Callable[[], str]] = None
    channel_use: Optional[Callable[[str], str]] = None
    channel_current: Optional[Callable[[], str]] = None
    tools_list: Optional[Callable[[], str]] = None
    tools_allow: Optional[Callable[[Optional[str], bool, Optional[str]], str]] = None
    tools_deny: Optional[Callable[[Optional[str], bool, Optional[str]], str]] = None


@dataclass
class ReplDispatchResult:
    handled: bool
    exit_requested: bool = False


@dataclass(frozen=True)
class CommandSpec:
    """Declarative slash command grammar metadata for execution + hints."""

    name: str
    subcommands: Tuple[str, ...] = field(default_factory=tuple)
    options: Tuple[str, ...] = field(default_factory=tuple)
    values: Tuple[str, ...] = field(default_factory=tuple)
    examples: Tuple[str, ...] = field(default_factory=tuple)


@dataclass
class HintResult:
    path: str
    suggestions: List[str]
    text: str
    fallback_to_text: bool = False


_TOP_LEVEL_COMMAND_SPECS: Dict[str, CommandSpec] = {
    "help": CommandSpec(name="help", examples=("/help",)),
    "clear": CommandSpec(name="clear", examples=("/clear",)),
    "pending": CommandSpec(name="pending", examples=("/pending",)),
    "choose": CommandSpec(name="choose", values=("<index|text>",), examples=("/choose 1", "/choose 自定义回答")),
    "session": CommandSpec(
        name="session",
        subcommands=("list", "new", "use", "current"),
        examples=("/session list", "/session use demo"),
    ),
    "doctor": CommandSpec(
        name="doctor",
        options=("--format", "--verbose"),
        examples=("/doctor --format text",),
    ),
    "mcp": CommandSpec(name="mcp", subcommands=("list", "reload", "status"), examples=("/mcp status",)),
    "skill": CommandSpec(name="skill", subcommands=("list", "reload"), examples=("/skill list",)),
    "policy": CommandSpec(
        name="policy",
        subcommands=("approvals",),
        examples=("/policy approvals list",),
    ),
    "service": CommandSpec(
        name="service",
        subcommands=("status", "rebind", "unpair", "channel", "tools"),
        examples=("/service status", "/service channel use imessage"),
    ),
    "save": CommandSpec(name="save", values=("<name 可选>",), examples=("/save demo",)),
    "discard": CommandSpec(name="discard", examples=("/discard",)),
    "exit": CommandSpec(name="exit", examples=("/exit",)),
    "quit": CommandSpec(name="quit", examples=("/quit",)),
}

_TOP_LEVEL_ORDER: Tuple[str, ...] = tuple(_TOP_LEVEL_COMMAND_SPECS.keys())
_MENU_ROOTS: Tuple[str, ...] = ("session", "doctor", "mcp", "skill", "policy", "service")
_PROVIDER_VALUES: Tuple[str, ...] = ("claude",)
_SESSION_NEW_OPTIONS: Tuple[str, ...] = ("--name", "--provider")
_SESSION_LIST_OPTIONS: Tuple[str, ...] = ("--all",)
_SESSION_USE_OPTIONS: Tuple[str, ...] = ("--all",)
_DOCTOR_OPTIONS: Tuple[str, ...] = ("--format", "--verbose")
_DOCTOR_FORMAT_VALUES: Tuple[str, ...] = ("json", "text")
_POLICY_APPROVAL_SUBCOMMANDS: Tuple[str, ...] = ("list", "reset")
_POLICY_RESET_OPTIONS: Tuple[str, ...] = ("--all", "--tool", "--risk")
_RISK_VALUES: Tuple[str, ...] = ("low", "medium", "high")
_SERVICE_SUBCOMMANDS: Tuple[str, ...] = ("status", "rebind", "unpair", "channel", "tools")
_SERVICE_CHANNEL_SUBCOMMANDS: Tuple[str, ...] = ("list", "use", "current")
_SERVICE_CHANNEL_VALUES: Tuple[str, ...] = ("imessage",)
_SERVICE_TOOLS_SUBCOMMANDS: Tuple[str, ...] = ("list", "allow", "deny")
_MCP_SUBCOMMANDS: Tuple[str, ...] = ("list", "reload", "status")


def dispatch_slash_command(
    raw_line: str,
    state: ReplState,
    stream: TextIO = sys.stdout,
) -> ReplDispatchResult:
    stripped = raw_line.strip()
    if not stripped.startswith("/"):
        return ReplDispatchResult(handled=False)

    text = stripped[1:].strip()
    if not text:
        _echo(stream, render_repl_help_summary())
        return ReplDispatchResult(handled=True)

    try:
        parts = shlex.split(text)
    except ValueError as exc:
        _echo(stream, render_notice("error", "命令解析失败：{0}".format(exc)))
        return ReplDispatchResult(handled=True)

    return _dispatch_parts(parts, state=state, stream=stream)


def execute_slash_command_to_text(raw_line: str, state: ReplState) -> Tuple[ReplDispatchResult, str]:
    """Run one slash command and capture textual output for TUI rendering."""

    stream = StringIO()
    result = dispatch_slash_command(raw_line=raw_line, state=state, stream=stream)
    return result, stream.getvalue().strip()


def build_slash_hint(raw_input: str, state: Optional[ReplState]) -> HintResult:
    """Build dynamic slash hint text from partial user input."""

    if not raw_input.startswith("/"):
        return HintResult(path="", suggestions=[], text="")

    body = raw_input[1:]
    tokens, trailing_space = _split_partial_tokens(body)
    if not tokens:
        return _hint_with(
            path="/",
            suggestions=["/{0}".format(cmd) for cmd in _TOP_LEVEL_ORDER],
            example="/help",
        )

    typed_root = tokens[0].lower()
    root_matches = _match_prefix(_TOP_LEVEL_ORDER, typed_root)
    if not root_matches:
        return _unknown_hint(path="/{0}".format(typed_root))

    chosen_root: Optional[str] = None
    if typed_root in _TOP_LEVEL_COMMAND_SPECS:
        chosen_root = typed_root
    elif len(root_matches) == 1:
        chosen_root = root_matches[0]
    elif len(tokens) == 1 and not trailing_space:
        return _hint_with(
            path="/{0}".format(typed_root),
            suggestions=["/{0}".format(item) for item in root_matches],
            example="/{0}".format(root_matches[0]),
        )

    if chosen_root is None:
        return _unknown_hint(path="/{0}".format(typed_root))

    return _hint_for_root(
        root=chosen_root,
        args=tokens[1:],
        trailing_space=trailing_space,
        state=state,
    )


def _split_partial_tokens(text: str) -> Tuple[List[str], bool]:
    trailing_space = bool(text) and text[-1].isspace()
    tokens: List[str] = []
    current: List[str] = []
    quote: Optional[str] = None
    escaped = False

    for char in text:
        if escaped:
            current.append(char)
            escaped = False
            continue

        if quote:
            if char == "\\":
                escaped = True
                continue
            if char == quote:
                quote = None
                continue
            current.append(char)
            continue

        if char in {"'", '"'}:
            quote = char
            continue

        if char.isspace():
            if current:
                tokens.append("".join(current))
                current = []
            continue
        current.append(char)

    if current or quote:
        tokens.append("".join(current))
    return tokens, trailing_space


def _match_prefix(candidates: Iterable[str], token: str) -> List[str]:
    normalized = token.lower().strip()
    if not normalized:
        return list(candidates)
    return [item for item in candidates if item.lower().startswith(normalized)]


def _hint_with(
    path: str,
    suggestions: List[str],
    *,
    example: Optional[str] = None,
    note: Optional[str] = None,
    fallback_to_text: bool = False,
) -> HintResult:
    uniq = _unique_preserve_order(suggestions)
    parts: List[str] = ["命令: {0}".format(path)]
    if uniq:
        parts.append("可选: {0}".format(" | ".join(uniq[:8])))
    if example:
        parts.append("示例: {0}".format(example))
    if note:
        parts.append(note)
    return HintResult(
        path=path,
        suggestions=uniq,
        text="  ·  ".join(parts),
        fallback_to_text=fallback_to_text,
    )


def _unknown_hint(path: str) -> HintResult:
    return _hint_with(
        path=path,
        suggestions=[],
        note="未识别命令，当前会按普通消息发送。",
        fallback_to_text=True,
    )


def _hint_for_root(
    root: str,
    args: List[str],
    trailing_space: bool,
    state: Optional[ReplState],
) -> HintResult:
    if root in {"help", "clear", "exit", "quit", "discard"}:
        return _hint_with(path="/{0}".format(root), suggestions=[], note="该命令不需要参数。")

    if root == "pending":
        return _hint_with(path="/pending", suggestions=[], note="查看当前待确认交互。")

    if root == "choose":
        return _hint_choose(args=args, trailing_space=trailing_space, state=state)

    if root == "save":
        if not args:
            return _hint_with(
                path="/save",
                suggestions=["<name 可选>"],
                example="/save demo",
            )
        return _hint_with(path="/save", suggestions=["<name 可选>"], note="回车执行保存。")

    if root == "session":
        return _hint_session(args=args, trailing_space=trailing_space, state=state)

    if root == "doctor":
        return _hint_doctor(args=args, trailing_space=trailing_space)

    if root == "mcp":
        return _hint_mcp(args=args, trailing_space=trailing_space)

    if root == "skill":
        return _hint_skill(args=args, trailing_space=trailing_space)

    if root == "policy":
        return _hint_policy(args=args, trailing_space=trailing_space)

    if root == "service":
        return _hint_service(args=args, trailing_space=trailing_space, state=state)

    return _unknown_hint(path="/{0}".format(root))


def _hint_session(args: List[str], trailing_space: bool, state: Optional[ReplState]) -> HintResult:
    subs = ("list", "new", "use", "current")
    if not args:
        return _hint_with(path="/session", suggestions=list(subs), example="/session use demo")

    sub_token = args[0].lower()
    sub_matches = _match_prefix(subs, sub_token)
    if sub_token not in subs:
        if len(sub_matches) != 1:
            return _hint_with(path="/session", suggestions=sub_matches, example="/session list")
        sub_token = sub_matches[0]

    rest = args[1:]
    if sub_token == "list":
        if not rest:
            return _hint_with(path="/session list", suggestions=list(_SESSION_LIST_OPTIONS), example="/session list --all")
        if not trailing_space and rest[-1].startswith("--"):
            matches = _match_prefix(_SESSION_LIST_OPTIONS, rest[-1])
            return _hint_with(path="/session list", suggestions=matches or list(_SESSION_LIST_OPTIONS))
        return _hint_with(path="/session list", suggestions=list(_SESSION_LIST_OPTIONS), note="该命令仅支持可选 --all。")

    if sub_token == "new":
        return _hint_session_new(rest=rest, trailing_space=trailing_space)

    if sub_token == "use":
        return _hint_session_use(rest=rest, trailing_space=trailing_space, state=state)

    return _hint_with(path="/session current", suggestions=[], note="该命令无参数。")


def _hint_choose(args: List[str], trailing_space: bool, state: Optional[ReplState]) -> HintResult:
    del trailing_space
    suggestions: List[str] = []
    hooks = state.interaction_hooks if state is not None else None
    if hooks is not None and callable(hooks.choice_suggestions):
        try:
            suggestions = [item for item in hooks.choice_suggestions() if item]
        except Exception:
            suggestions = []
    if not suggestions:
        suggestions = ["<index>", "<自定义文本>"]
    if not args:
        return _hint_with(
            path="/choose",
            suggestions=suggestions,
            example="/choose 1",
        )
    return _hint_with(
        path="/choose",
        suggestions=suggestions,
        note="回车提交交互回答。",
    )


def _hint_session_new(rest: List[str], trailing_space: bool) -> HintResult:
    if not rest:
        return _hint_with(
            path="/session new",
            suggestions=list(_SESSION_NEW_OPTIONS),
            example="/session new --name demo",
        )

    if trailing_space and rest[-1] == "--name":
        return _hint_with(path="/session new", suggestions=["<alias>"], example="/session new --name demo")
    if trailing_space and rest[-1] == "--provider":
        return _hint_with(path="/session new", suggestions=list(_PROVIDER_VALUES), example="/session new --provider claude")

    if len(rest) >= 2 and rest[-2] == "--provider" and not trailing_space:
        matches = _match_prefix(_PROVIDER_VALUES, rest[-1])
        return _hint_with(path="/session new", suggestions=matches or list(_PROVIDER_VALUES))

    if len(rest) >= 2 and rest[-2] == "--name" and not trailing_space:
        return _hint_with(path="/session new", suggestions=["<alias>"], note="名称可自定义。")

    if not trailing_space and rest[-1].startswith("--"):
        matches = _match_prefix(_SESSION_NEW_OPTIONS, rest[-1])
        return _hint_with(path="/session new", suggestions=matches or list(_SESSION_NEW_OPTIONS))

    used = {token for token in rest if token in _SESSION_NEW_OPTIONS}
    remaining = [opt for opt in _SESSION_NEW_OPTIONS if opt not in used]
    if remaining:
        return _hint_with(path="/session new", suggestions=remaining)
    return _hint_with(path="/session new", suggestions=[], note="参数已齐全，回车执行。")


def _hint_session_use(rest: List[str], trailing_space: bool, state: Optional[ReplState]) -> HintResult:
    include_ephemeral = "--all" in rest
    ref_prefix = ""
    if not trailing_space:
        for token in rest:
            if token.startswith("--"):
                continue
            ref_prefix = token

    if not trailing_space and rest and rest[-1].startswith("--"):
        matches = _match_prefix(_SESSION_USE_OPTIONS, rest[-1])
        return _hint_with(path="/session use", suggestions=matches or list(_SESSION_USE_OPTIONS))

    candidates = _session_ref_candidates(
        state=state,
        include_ephemeral=include_ephemeral,
        prefix=ref_prefix,
    )
    suggestions: List[str] = []
    if "--all" not in rest:
        suggestions.append("--all")
    suggestions.extend(candidates)
    if not suggestions:
        suggestions.append("<session_ref>")
    return _hint_with(path="/session use", suggestions=suggestions, example="/session use demo")


def _hint_doctor(args: List[str], trailing_space: bool) -> HintResult:
    if not args:
        return _hint_with(path="/doctor", suggestions=list(_DOCTOR_OPTIONS), example="/doctor --format text")

    if trailing_space and args[-1] == "--format":
        return _hint_with(path="/doctor", suggestions=list(_DOCTOR_FORMAT_VALUES))

    if len(args) >= 2 and args[-2] == "--format" and not trailing_space:
        matches = _match_prefix(_DOCTOR_FORMAT_VALUES, args[-1])
        return _hint_with(path="/doctor", suggestions=matches or list(_DOCTOR_FORMAT_VALUES))

    if not trailing_space and args[-1].startswith("--"):
        matches = _match_prefix(_DOCTOR_OPTIONS, args[-1])
        return _hint_with(path="/doctor", suggestions=matches or list(_DOCTOR_OPTIONS))

    used = {token for token in args if token in _DOCTOR_OPTIONS}
    remaining = [opt for opt in _DOCTOR_OPTIONS if opt not in used]
    return _hint_with(path="/doctor", suggestions=remaining or list(_DOCTOR_OPTIONS))


def _hint_skill(args: List[str], trailing_space: bool) -> HintResult:
    del trailing_space
    if not args:
        return _hint_with(path="/skill", suggestions=["list", "reload"], example="/skill list")

    matches = _match_prefix(("list", "reload"), args[0].lower())
    if matches:
        return _hint_with(path="/skill", suggestions=matches, example="/skill list")
    return _unknown_hint(path="/skill {0}".format(args[0]))


def _hint_mcp(args: List[str], trailing_space: bool) -> HintResult:
    del trailing_space
    if not args:
        return _hint_with(path="/mcp", suggestions=list(_MCP_SUBCOMMANDS), example="/mcp status")

    matches = _match_prefix(_MCP_SUBCOMMANDS, args[0].lower())
    if matches:
        return _hint_with(path="/mcp", suggestions=matches, example="/mcp list")
    return _unknown_hint(path="/mcp {0}".format(args[0]))


def _hint_policy(args: List[str], trailing_space: bool) -> HintResult:
    if not args:
        return _hint_with(path="/policy", suggestions=["approvals"], example="/policy approvals list")

    approvals_token = args[0].lower()
    matches = _match_prefix(("approvals",), approvals_token)
    if approvals_token != "approvals":
        if matches:
            return _hint_with(path="/policy", suggestions=matches, example="/policy approvals list")
        return _unknown_hint(path="/policy {0}".format(approvals_token))

    rest = args[1:]
    if not rest:
        return _hint_with(path="/policy approvals", suggestions=list(_POLICY_APPROVAL_SUBCOMMANDS))

    sub_token = rest[0].lower()
    sub_matches = _match_prefix(_POLICY_APPROVAL_SUBCOMMANDS, sub_token)
    if sub_token not in _POLICY_APPROVAL_SUBCOMMANDS:
        if sub_matches:
            return _hint_with(path="/policy approvals", suggestions=sub_matches)
        return _unknown_hint(path="/policy approvals {0}".format(sub_token))

    if sub_token == "list":
        return _hint_with(path="/policy approvals list", suggestions=[], note="该命令无参数。")

    # reset
    reset_rest = rest[1:]
    return _hint_policy_reset(reset_rest=reset_rest, trailing_space=trailing_space)


def _hint_policy_reset(reset_rest: List[str], trailing_space: bool) -> HintResult:
    if not reset_rest:
        return _hint_with(path="/policy approvals reset", suggestions=list(_POLICY_RESET_OPTIONS))

    if trailing_space and reset_rest[-1] == "--risk":
        return _hint_with(path="/policy approvals reset", suggestions=list(_RISK_VALUES))
    if trailing_space and reset_rest[-1] == "--tool":
        return _hint_with(path="/policy approvals reset", suggestions=["<tool_name>"])

    if len(reset_rest) >= 2 and reset_rest[-2] == "--risk" and not trailing_space:
        matches = _match_prefix(_RISK_VALUES, reset_rest[-1])
        return _hint_with(path="/policy approvals reset", suggestions=matches or list(_RISK_VALUES))
    if len(reset_rest) >= 2 and reset_rest[-2] == "--tool" and not trailing_space:
        return _hint_with(path="/policy approvals reset", suggestions=["<tool_name>"])

    if not trailing_space and reset_rest[-1].startswith("--"):
        matches = _match_prefix(_POLICY_RESET_OPTIONS, reset_rest[-1])
        return _hint_with(path="/policy approvals reset", suggestions=matches or list(_POLICY_RESET_OPTIONS))

    used = {token for token in reset_rest if token in _POLICY_RESET_OPTIONS}
    remaining = [opt for opt in _POLICY_RESET_OPTIONS if opt not in used]
    if remaining:
        return _hint_with(path="/policy approvals reset", suggestions=remaining)
    return _hint_with(path="/policy approvals reset", suggestions=[], note="参数已齐全，回车执行。")


def _hint_service(args: List[str], trailing_space: bool, state: Optional[ReplState]) -> HintResult:
    if not args:
        return _hint_with(
            path="/service",
            suggestions=list(_SERVICE_SUBCOMMANDS),
            example="/service status",
        )

    token = args[0].lower()
    matches = _match_prefix(_SERVICE_SUBCOMMANDS, token)
    if token not in _SERVICE_SUBCOMMANDS:
        if matches:
            return _hint_with(path="/service", suggestions=matches, example="/service rebind")
        return _unknown_hint(path="/service {0}".format(token))

    if token in {"status", "rebind", "unpair"}:
        return _hint_with(path="/service {0}".format(token), suggestions=[], note="该命令无参数。")

    if token == "tools":
        rest = args[1:]
        return _hint_service_tools(rest=rest, trailing_space=trailing_space, state=state)

    # /service channel ...
    rest = args[1:]
    if not rest:
        return _hint_with(
            path="/service channel",
            suggestions=list(_SERVICE_CHANNEL_SUBCOMMANDS),
            example="/service channel use imessage",
        )

    channel_sub = rest[0].lower()
    channel_matches = _match_prefix(_SERVICE_CHANNEL_SUBCOMMANDS, channel_sub)
    if channel_sub not in _SERVICE_CHANNEL_SUBCOMMANDS:
        if channel_matches:
            return _hint_with(path="/service channel", suggestions=channel_matches)
        return _unknown_hint(path="/service channel {0}".format(channel_sub))

    if channel_sub in {"list", "current"}:
        return _hint_with(path="/service channel {0}".format(channel_sub), suggestions=[], note="该命令无参数。")

    use_rest = rest[1:]
    if not use_rest:
        return _hint_with(
            path="/service channel use",
            suggestions=list(_SERVICE_CHANNEL_VALUES),
            example="/service channel use imessage",
        )

    if not trailing_space:
        matched_values = _match_prefix(_SERVICE_CHANNEL_VALUES, use_rest[-1].lower())
        return _hint_with(
            path="/service channel use",
            suggestions=matched_values or list(_SERVICE_CHANNEL_VALUES),
            example="/service channel use imessage",
        )

    return _hint_with(path="/service channel use", suggestions=[], note="参数已齐全，回车执行。")


def _hint_service_tools(rest: List[str], trailing_space: bool, state: Optional[ReplState]) -> HintResult:
    if not rest:
        return _hint_with(
            path="/service tools",
            suggestions=list(_SERVICE_TOOLS_SUBCOMMANDS),
            example="/service tools allow shell.exec",
        )

    sub = rest[0].lower()
    sub_matches = _match_prefix(_SERVICE_TOOLS_SUBCOMMANDS, sub)
    if sub not in _SERVICE_TOOLS_SUBCOMMANDS:
        if sub_matches:
            return _hint_with(path="/service tools", suggestions=sub_matches)
        return _unknown_hint(path="/service tools {0}".format(sub))

    if sub == "list":
        return _hint_with(path="/service tools list", suggestions=[], note="该命令无参数。")

    action_rest = rest[1:]
    if not action_rest:
        return _hint_with(
            path="/service tools {0}".format(sub),
            suggestions=["--all", "<tool_name>", "--risk"],
            example="/service tools {0} shell.exec --risk low".format(sub),
        )

    if trailing_space and action_rest[-1] == "--risk":
        return _hint_with(
            path="/service tools {0}".format(sub),
            suggestions=list(_RISK_VALUES),
        )

    if len(action_rest) >= 2 and action_rest[-2] == "--risk" and not trailing_space:
        matches = _match_prefix(_RISK_VALUES, action_rest[-1].lower())
        return _hint_with(
            path="/service tools {0}".format(sub),
            suggestions=matches or list(_RISK_VALUES),
        )

    if not trailing_space and action_rest[-1].startswith("--"):
        matches = _match_prefix(("--all", "--risk"), action_rest[-1])
        return _hint_with(
            path="/service tools {0}".format(sub),
            suggestions=matches or ["--all", "--risk"],
        )

    prefix = ""
    if not trailing_space:
        for token in action_rest:
            if token.startswith("--"):
                continue
            prefix = token
            break

    candidates = _service_tool_candidates(state=state, prefix=prefix)
    suggestions: List[str] = []
    if "--all" not in action_rest:
        suggestions.append("--all")
    if "--risk" not in action_rest:
        suggestions.append("--risk")
    suggestions.extend(candidates)
    if not suggestions:
        suggestions.append("<tool_name>")
    return _hint_with(
        path="/service tools {0}".format(sub),
        suggestions=suggestions,
        example="/service tools {0} shell.exec".format(sub),
    )


def _session_ref_candidates(
    *,
    state: Optional[ReplState],
    include_ephemeral: bool,
    prefix: str,
) -> List[str]:
    if state is None:
        return []

    try:
        settings = load_settings(context_id=state.context_id, provider=state.provider)
        store = SessionStore(settings.context_dir / "sessions.db")
    except Exception:
        return []

    try:
        sessions = store.list_sessions(
            context_id=settings.context_id,
            include_ephemeral=include_ephemeral,
        )
    finally:
        store.close()

    refs: List[str] = []
    for session in sessions:
        if session.name:
            refs.append(session.name)
        refs.append(session.session_id[:16])

    if prefix:
        refs = [item for item in refs if item.lower().startswith(prefix.lower())]
    return _unique_preserve_order(refs)[:8]


def _service_tool_candidates(
    *,
    state: Optional[ReplState],
    prefix: str,
) -> List[str]:
    if state is None:
        return []
    try:
        settings = load_settings(context_id=state.context_id, provider=state.provider)
        runtime = Runtime(settings)
    except Exception:
        return []

    try:
        refs = runtime.registry.list_tool_ids()
    finally:
        runtime.close()

    if prefix:
        refs = [item for item in refs if item.lower().startswith(prefix.lower())]
    return _unique_preserve_order(refs)[:8]


def _unique_preserve_order(items: Iterable[str]) -> List[str]:
    seen: Dict[str, bool] = {}
    ordered: List[str] = []
    for item in items:
        if item in seen:
            continue
        seen[item] = True
        ordered.append(item)
    return ordered


def _dispatch_parts(parts: List[str], state: ReplState, stream: TextIO) -> ReplDispatchResult:
    if not parts:
        return ReplDispatchResult(handled=True)

    root = parts[0].lower()
    args = parts[1:]

    if root in {"exit", "quit"}:
        return ReplDispatchResult(handled=True, exit_requested=True)

    if root == "help":
        _echo(stream, render_repl_help_summary())
        return ReplDispatchResult(handled=True)

    if root == "clear":
        _handle_clear(state=state, stream=stream)
        return ReplDispatchResult(handled=True)

    if root == "pending":
        _handle_pending(state=state, stream=stream)
        return ReplDispatchResult(handled=True)

    if root == "choose":
        _handle_choose(args=args, state=state, stream=stream)
        return ReplDispatchResult(handled=True)

    if root == "save":
        _handle_save(args=args, state=state, stream=stream)
        return ReplDispatchResult(handled=True)

    if root == "discard":
        _handle_discard(state=state, stream=stream)
        return ReplDispatchResult(handled=True)

    if root == "model":
        _echo(
            stream,
            render_notice(
                "error",
                "命令 `/model` 已移除。当前默认模型为 claude，可在配置文件中调整。",
                "Command `/model` was removed. Default model is claude.",
            ),
        )
        return ReplDispatchResult(handled=True)

    if root in _MENU_ROOTS:
        if not args:
            _dispatch_menu(root=root, stream=stream)
            return ReplDispatchResult(handled=True)
        return _dispatch_known(root=root, args=args, state=state, stream=stream)

    # Unknown slash command should fall back to normal model input.
    return ReplDispatchResult(handled=False)


def _dispatch_menu(root: str, stream: TextIO) -> None:
    menu_lines: Dict[str, str] = {
        "session": "list [--all] | new [--name NAME] [--provider claude] | use <ref> | current",
        "doctor": "--format json|text [--verbose]",
        "mcp": "list | reload | status",
        "skill": "list | reload",
        "policy": "approvals list | approvals reset --all | approvals reset --tool T --risk R",
        "service": "status | rebind | unpair | channel list|use <id>|current | tools list|allow|deny",
    }
    _echo(
        stream,
        render_notice(
            "info",
            "命令菜单：/{0} {1}".format(root, menu_lines[root]),
            "Command menu",
        ),
    )


def _dispatch_known(root: str, args: List[str], state: ReplState, stream: TextIO) -> ReplDispatchResult:
    try:
        if root == "session":
            _handle_session(args=args, state=state, stream=stream)
        elif root == "doctor":
            _handle_doctor(args=args, state=state, stream=stream)
        elif root == "mcp":
            _handle_mcp(args=args, state=state, stream=stream)
        elif root == "skill":
            _handle_skill(args=args, state=state, stream=stream)
        elif root == "policy":
            _handle_policy(args=args, state=state, stream=stream)
        elif root == "service":
            _handle_service(args=args, state=state, stream=stream)
    except Exception as exc:  # pragma: no cover - defensive
        _echo(stream, render_notice("error", "命令执行失败：{0}".format(exc)))
    return ReplDispatchResult(handled=True)


def _handle_service(args: List[str], state: ReplState, stream: TextIO) -> None:
    hooks = state.service_hooks
    if hooks is None:
        _echo(
            stream,
            render_notice(
                "warn",
                "当前不在 service 模式，无法执行 /service 命令。",
                "Service command is only available in service mode.",
            ),
        )
        return

    sub = args[0].lower()
    if sub == "status":
        _echo(stream, hooks.status())
        return
    if sub == "rebind":
        _echo(stream, hooks.rebind())
        return
    if sub == "unpair":
        _echo(stream, hooks.unpair())
        return
    if sub == "channel":
        _handle_service_channel(args=args[1:], hooks=hooks, stream=stream)
        return
    if sub == "tools":
        _handle_service_tools(args=args[1:], hooks=hooks, stream=stream)
        return

    _echo(
        stream,
        render_notice(
            "error",
            "不支持的 service 子命令：{0}".format(sub),
            "Unsupported service command.",
        ),
    )


def _handle_service_tools(args: List[str], hooks: ServiceCommandHooks, stream: TextIO) -> None:
    if not args:
        _echo(
            stream,
            render_notice(
                "error",
                "请提供子命令：list|allow|deny。",
                "Expected: list|allow|deny.",
            ),
        )
        return

    sub = args[0].lower()
    if sub == "list":
        if callable(hooks.tools_list):
            _echo(stream, hooks.tools_list())
            return
        _echo(stream, render_notice("warn", "当前环境不支持工具策略查看。", "Tool policy list is unavailable."))
        return

    if sub not in {"allow", "deny"}:
        _echo(
            stream,
            render_notice(
                "error",
                "不支持的 tools 子命令：{0}".format(sub),
                "Unsupported tools command.",
            ),
        )
        return

    opts, positional = _parse_options(args[1:])
    apply_all = bool(opts.get("--all"))
    risk = opts.get("--risk")
    if risk is not None:
        risk = str(risk)

    tool_name: Optional[str] = None
    if positional:
        tool_name = str(positional[0])

    if apply_all and tool_name:
        _echo(
            stream,
            render_notice(
                "error",
                "不能同时指定工具名和 --all。",
                "Cannot use tool name together with --all.",
            ),
        )
        return
    if not apply_all and not tool_name:
        _echo(
            stream,
            render_notice(
                "error",
                "请提供工具名，或使用 --all。",
                "Provide tool name or use --all.",
            ),
        )
        return

    if sub == "allow":
        if callable(hooks.tools_allow):
            _echo(stream, hooks.tools_allow(tool_name, apply_all, risk))
            return
        _echo(stream, render_notice("warn", "当前环境不支持工具策略修改。", "Tool policy update is unavailable."))
        return

    if callable(hooks.tools_deny):
        _echo(stream, hooks.tools_deny(tool_name, apply_all, risk))
        return
    _echo(stream, render_notice("warn", "当前环境不支持工具策略修改。", "Tool policy update is unavailable."))


def _handle_service_channel(args: List[str], hooks: ServiceCommandHooks, stream: TextIO) -> None:
    if not args:
        _echo(
            stream,
            render_notice(
                "error",
                "请提供子命令：list|use <id>|current。",
                "Expected: list|use <id>|current.",
            ),
        )
        return

    sub = args[0].lower()
    if sub == "list":
        if callable(hooks.channel_list):
            _echo(stream, hooks.channel_list())
            return
        _echo(stream, render_notice("warn", "当前环境不支持渠道列表。", "Channel list is unavailable."))
        return

    if sub == "current":
        if callable(hooks.channel_current):
            _echo(stream, hooks.channel_current())
            return
        _echo(stream, render_notice("warn", "当前环境不支持查询当前渠道。", "Channel current is unavailable."))
        return

    if sub == "use":
        if len(args) < 2:
            _echo(stream, render_notice("error", "请提供渠道 ID，例如 imessage。", "Channel id is required."))
            return
        if callable(hooks.channel_use):
            _echo(stream, hooks.channel_use(args[1]))
            return
        _echo(stream, render_notice("warn", "当前环境不支持激活渠道。", "Channel activation is unavailable."))
        return

    _echo(
        stream,
        render_notice(
            "error",
            "不支持的 channel 子命令：{0}".format(sub),
            "Unsupported channel command.",
        ),
    )


def _handle_save(args: List[str], state: ReplState, stream: TextIO) -> None:
    settings = load_settings(context_id=state.context_id, provider=state.provider)
    runtime = Runtime(settings)
    try:
        current = _resolve_current_session(runtime=runtime, state=state)
        if current is None:
            _echo(stream, render_notice("error", "当前没有可保存会话。", "No active session to save."))
            return

        requested_name = args[0] if args else None
        saved = runtime.session_store.save_session(
            session_id=current.session_id,
            name=requested_name,
        )
        state.session_ref = saved.session_id
        state.session_name = saved.name
        state.session_is_ephemeral = saved.is_ephemeral
        runtime.session_store.set_current_session(runtime.context_id, saved.session_id)
        _echo(
            stream,
            render_notice(
                "success",
                "会话已保存：{0} name={1}".format(saved.session_id, saved.name or ""),
                "Session saved.",
            ),
        )
    finally:
        runtime.close()


def _handle_pending(state: ReplState, stream: TextIO) -> None:
    hooks = state.interaction_hooks
    if hooks is None:
        _echo(stream, render_notice("info", "当前无待确认交互。", "No pending interaction."))
        return
    try:
        _echo(stream, hooks.pending())
    except Exception as exc:
        _echo(stream, render_notice("error", "读取待确认交互失败：{0}".format(exc), "Failed to read pending interaction."))


def _handle_choose(args: List[str], state: ReplState, stream: TextIO) -> None:
    hooks = state.interaction_hooks
    if hooks is None:
        _echo(stream, render_notice("error", "当前无待确认交互。", "No pending interaction."))
        return
    choice = " ".join(args).strip()
    if not choice:
        _echo(stream, render_notice("error", "请提供编号或文本，例如 `/choose 1`。", "Provide option index or text, e.g. `/choose 1`."))
        return
    try:
        _echo(stream, hooks.choose(choice, "local"))
    except Exception as exc:
        _echo(stream, render_notice("error", "提交交互回答失败：{0}".format(exc), "Failed to submit interaction answer."))


def _handle_discard(state: ReplState, stream: TextIO) -> None:
    settings = load_settings(context_id=state.context_id, provider=state.provider)
    runtime = Runtime(settings)
    try:
        current = _resolve_current_session(runtime=runtime, state=state)
        if current is None:
            _echo(stream, render_notice("error", "当前没有会话可丢弃。", "No session to discard."))
            return

        if not runtime.session_store.is_unsaved_ephemeral(current.session_id):
            _echo(
                stream,
                render_notice(
                    "warn",
                    "当前会话不是未保存临时会话，已拒绝丢弃。",
                    "Current session is not an unsaved temporary session.",
                ),
            )
            return

        runtime.session_store.discard_session(current.session_id)
        replacement = runtime.session_store.create_session(
            context_id=runtime.context_id,
            is_ephemeral=True,
        )
        runtime.session_store.set_current_session(runtime.context_id, replacement.session_id)

        state.session_ref = replacement.session_id
        state.session_name = replacement.name
        state.session_is_ephemeral = replacement.is_ephemeral
        _echo(
            stream,
            render_notice(
                "success",
                "已丢弃临时会话并创建新临时会话：{0}".format(replacement.session_id),
                "Temporary session discarded and replaced.",
            ),
        )
    finally:
        runtime.close()


def _handle_clear(state: ReplState, stream: TextIO) -> None:
    settings = load_settings(context_id=state.context_id, provider=state.provider)
    runtime = Runtime(settings)
    try:
        current = _resolve_current_session(runtime=runtime, state=state)
        if current is None:
            _echo(stream, render_notice("info", "当前没有会话可清空。", "No session context to clear."))
            return

        counts = clear_session_context(
            runtime.session_store,
            context_id=runtime.context_id,
            session_id=current.session_id,
        )
        runtime.session_store.touch_session(current.session_id)
        runtime.emit(
            "context.cleared",
            {
                "session_id": current.session_id,
                "deleted_messages": counts["deleted_messages"],
                "deleted_summaries": counts["deleted_summaries"],
            },
            conversation_id="session.{0}".format(current.session_id),
            actor="repl",
        )

        state.session_ref = current.session_id
        state.session_name = current.name
        state.session_is_ephemeral = current.is_ephemeral
        _echo(
            stream,
            render_notice(
                "success",
                "已清空当前会话上下文：messages={0} summaries={1}".format(
                    counts["deleted_messages"],
                    counts["deleted_summaries"],
                ),
                "Session context cleared.",
            ),
        )
    finally:
        runtime.close()


def _handle_session(args: List[str], state: ReplState, stream: TextIO) -> None:
    sub = args[0].lower()
    settings = load_settings(context_id=state.context_id, provider=state.provider)

    if sub == "list":
        _session_list(settings=settings, include_all="--all" in args, stream=stream)
        return

    runtime = Runtime(settings)
    try:
        if sub == "new":
            opts, _positional = _parse_options(args[1:])
            name = opts.get("--name")
            provider = opts.get("--provider")
            normalized_provider = str(provider or "").strip().lower()
            if normalized_provider and normalized_provider not in ALLOWED_PROVIDERS:
                _echo(
                    stream,
                    render_notice(
                        "error",
                        "不支持的 provider：{0}，当前仅支持：claude。".format(provider),
                        "Unsupported provider: {0}. Supported: claude.".format(provider),
                    ),
                )
                return
            provider_to_lock = normalized_provider or runtime.settings.provider
            session = runtime.session_store.create_session(
                context_id=runtime.context_id,
                name=name,
                provider_locked=provider_to_lock,
                is_ephemeral=False,
            )
            runtime.session_store.set_current_session(runtime.context_id, session.session_id)
            state.session_ref = session.session_id
            state.session_name = session.name
            state.session_is_ephemeral = session.is_ephemeral
            _echo(
                stream,
                "已创建会话 (Created session): {0} name={1} provider={2}".format(
                    session.session_id,
                    session.name or "",
                    session.provider_locked or "",
                ),
            )
            return

        if sub == "use":
            if len(args) < 2:
                _echo(stream, render_notice("error", "请提供会话引用。", "Session ref is required."))
                return
            session = runtime.session_store.resolve_session_ref(runtime.context_id, args[1])
            runtime.session_store.set_current_session(runtime.context_id, session.session_id)
            state.session_ref = session.session_id
            state.session_name = session.name
            state.session_is_ephemeral = session.is_ephemeral
            _echo(
                stream,
                "当前会话 (Current session): {0} name={1} provider={2}".format(
                    session.session_id,
                    session.name or "",
                    session.provider_locked or "",
                ),
            )
            return

        if sub == "current":
            current = runtime.session_store.get_current_session(runtime.context_id)
            if current is None:
                _echo(stream, render_notice("info", "当前没有会话。", "No current session."))
                return
            state.session_ref = current.session_id
            state.session_name = current.name
            state.session_is_ephemeral = current.is_ephemeral
            _echo(
                stream,
                "当前会话 (Current session): {0} name={1} provider={2} ephemeral={3}".format(
                    current.session_id,
                    current.name or "",
                    current.provider_locked or "",
                    "yes" if current.is_ephemeral else "no",
                ),
            )
            return

        _echo(stream, render_notice("error", "不支持的 session 子命令：{0}".format(sub), "Unsupported session command."))
    finally:
        runtime.close()


def _session_list(settings: object, include_all: bool, stream: TextIO) -> None:
    if include_all:
        contexts_root = settings.config_root / "contexts"
        sessions: List[SessionRecord] = []
        current_by_context: Dict[str, Optional[str]] = {}

        if contexts_root.exists() and contexts_root.is_dir():
            for context_dir in sorted(contexts_root.iterdir()):
                if not context_dir.is_dir():
                    continue
                ctx_id = context_dir.name
                store = SessionStore(context_dir / "sessions.db")
                try:
                    sessions.extend(
                        store.list_sessions(context_id=ctx_id, include_ephemeral=True)
                    )
                    current = store.get_current_session(ctx_id)
                    current_by_context[ctx_id] = current.session_id if current else None
                finally:
                    store.close()

        if not sessions:
            _echo(stream, render_notice("info", "未找到会话。", "No sessions found."))
            return

        for session in sessions:
            _echo(
                stream,
                _format_session_line(
                    session=session,
                    current_id=current_by_context.get(session.context_id),
                    include_context=True,
                ),
            )
        return

    runtime = Runtime(settings)
    try:
        sessions = runtime.session_store.list_sessions(
            context_id=runtime.context_id,
            include_ephemeral=False,
        )
        if not sessions:
            _echo(stream, render_notice("info", "未找到会话。", "No sessions found."))
            return

        current = runtime.session_store.get_current_session(runtime.context_id)
        current_id = current.session_id if current else None
        for session in sessions:
            _echo(
                stream,
                _format_session_line(
                    session=session,
                    current_id=current_id,
                    include_context=False,
                ),
            )
    finally:
        runtime.close()


def _handle_doctor(args: List[str], state: ReplState, stream: TextIO) -> None:
    opts, _positional = _parse_options(args)
    output_format = str(opts.get("--format") or "text").strip().lower()
    verbose = bool(opts.get("--verbose"))
    if output_format not in {"json", "text"}:
        _echo(stream, render_notice("error", "不支持的格式：{0}".format(output_format), "Unsupported format."))
        return

    settings = load_settings(context_id=state.context_id, provider=state.provider)
    runtime = Runtime(settings)
    try:
        report = runtime.doctor(verbose=verbose)
        if output_format == "json":
            _echo(stream, json.dumps(report, ensure_ascii=True, indent=2))
            return
        _echo(stream, render_doctor_text(report))
    finally:
        runtime.close()


def _handle_skill(args: List[str], state: ReplState, stream: TextIO) -> None:
    sub = args[0].lower()
    settings = load_settings(context_id=state.context_id, provider=state.provider)
    runtime = Runtime(settings)
    try:
        if sub == "list":
            skills = runtime.skill_engine.list_skills()
            if not skills:
                _echo(stream, render_notice("info", "当前未加载任何技能。", "No skills loaded."))
                return
            for skill in skills:
                _echo(
                    stream,
                    "{0} priority={1} triggers={2} source={3}".format(
                        skill.skill_id,
                        skill.priority,
                        ",".join(skill.triggers),
                        skill.source_path,
                    ),
                )
            return

        if sub == "reload":
            report = runtime.skill_engine.reload()
            _echo(
                stream,
                render_notice(
                    "success",
                    "技能已重载：loaded={0} errors={1}".format(len(report.skills), len(report.errors)),
                    "Reloaded skills: loaded={0} errors={1}".format(len(report.skills), len(report.errors)),
                ),
            )
            return
    finally:
        runtime.close()

    _echo(stream, render_notice("error", "不支持的 skill 子命令：{0}".format(sub), "Unsupported skill command."))


def _handle_mcp(args: List[str], state: ReplState, stream: TextIO) -> None:
    sub = args[0].lower()
    settings = load_settings(context_id=state.context_id, provider=state.provider)
    runtime = Runtime(settings)
    try:
        if sub == "status":
            status = runtime.mcp_manager.status()
            _echo(
                stream,
                "mcp loaded_servers={0} failed_servers={1} tools={2} config={3}".format(
                    int(status.get("loaded_servers") or 0),
                    int(status.get("failed_servers") or 0),
                    int(status.get("tool_count") or 0),
                    status.get("config_file") or "",
                ),
            )
            errors = status.get("errors")
            if isinstance(errors, dict):
                for key in sorted(errors.keys()):
                    _echo(stream, "error {0}: {1}".format(key, errors[key]))
            return

        if sub == "list":
            specs = runtime.mcp_manager.list_tool_specs()
            if not specs:
                _echo(stream, render_notice("info", "当前无 MCP tools。", "No MCP tools loaded."))
                return
            for spec in specs:
                _echo(
                    stream,
                    "{0} desc={1}".format(
                        spec.qualified_name,
                        spec.description or "",
                    ),
                )
            return

        if sub == "reload":
            report = runtime.reload_mcp()
            _echo(
                stream,
                render_notice(
                    "success",
                    "MCP 已重载：loaded_servers={0} failed_servers={1} tools={2}".format(
                        report.loaded_servers,
                        report.failed_servers,
                        report.tool_count,
                    ),
                    "MCP reloaded.",
                ),
            )
            return
    finally:
        runtime.close()

    _echo(stream, render_notice("error", "不支持的 mcp 子命令：{0}".format(sub), "Unsupported mcp command."))


def _handle_policy(args: List[str], state: ReplState, stream: TextIO) -> None:
    if len(args) < 2 or args[0].lower() != "approvals":
        _echo(
            stream,
            render_notice(
                "error",
                "仅支持 /policy approvals ...",
                "Only /policy approvals ... is supported.",
            ),
        )
        return

    sub = args[1].lower()
    opts, _positional = _parse_options(args[2:])

    settings = load_settings(context_id=state.context_id, provider=state.provider)
    runtime = Runtime(settings)
    try:
        if sub == "list":
            rows = runtime.approval_store.list_policies()
            if not rows:
                _echo(stream, render_notice("info", "暂无已持久化审批偏好。", "No persisted approval preferences."))
                return
            for row in rows:
                _echo(
                    stream,
                    "tool={0} risk={1} policy={2}".format(
                        row["tool_name"],
                        row["risk_tier"],
                        row["policy"],
                    ),
                )
            return

        if sub == "reset":
            if opts.get("--all"):
                deleted = runtime.approval_store.reset_all()
                _echo(
                    stream,
                    render_notice(
                        "success",
                        "已重置 {0} 条审批偏好。".format(deleted),
                        "Reset {0} approval preference(s).".format(deleted),
                    ),
                )
                return

            tool = opts.get("--tool")
            risk = opts.get("--risk")
            if not tool or not risk:
                _echo(
                    stream,
                    render_notice(
                        "error",
                        "请使用 --all，或同时提供 --tool 与 --risk。",
                        "Either --all or both --tool and --risk are required.",
                    ),
                )
                return

            deleted = runtime.approval_store.reset(tool_name=tool, risk_tier=risk)
            _echo(
                stream,
                render_notice(
                    "success",
                    "已重置 {0} 条审批偏好。".format(deleted),
                    "Reset {0} approval preference(s).".format(deleted),
                ),
            )
            return

        _echo(stream, render_notice("error", "不支持的 approvals 子命令：{0}".format(sub), "Unsupported approvals command."))
    finally:
        runtime.close()


def _resolve_current_session(runtime: Runtime, state: ReplState) -> Optional[SessionRecord]:
    if state.session_ref:
        current = runtime.session_store.get_session(state.session_ref)
        if current and current.context_id == runtime.context_id:
            return current
    return runtime.session_store.get_current_session(runtime.context_id)


def _parse_options(tokens: List[str]) -> Tuple[Dict[str, object], List[str]]:
    options: Dict[str, object] = {}
    positional: List[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in {"--all", "--verbose"}:
            options[token] = True
            i += 1
            continue
        if token in {"--name", "--provider", "--tool", "--risk", "--format"}:
            if i + 1 >= len(tokens):
                raise ValueError("missing value for option: {0}".format(token))
            options[token] = tokens[i + 1]
            i += 2
            continue
        positional.append(token)
        i += 1
    return options, positional


def _format_session_line(session: SessionRecord, current_id: Optional[str], include_context: bool) -> str:
    marker = "*" if current_id and current_id == session.session_id else " "
    context_part = " context={0}".format(session.context_id) if include_context else ""
    ephemeral_part = " ephemeral={0}".format("yes" if session.is_ephemeral else "no")
    return "{0} {1} name={2} provider={3}{4}{5}".format(
        marker,
        session.session_id,
        session.name or "",
        session.provider_locked or "",
        context_part,
        ephemeral_part,
    )


def _echo(stream: TextIO, text: str) -> None:
    stream.write(text + "\n")
    stream.flush()
