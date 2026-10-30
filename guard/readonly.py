"""PreToolUse hook that keeps an expert reviewer's Bash away from GitHub and remotes.

Claude Code runs it before each Bash call of a reviewer subagent, with the call as JSON on
stdin. Exit 0 lets the command run; exit 2 blocks it and shows stderr to the reviewer.
It blocks pushes, GitHub writes through `gh`, HTTP writes through `curl`/`wget`, and a
shell run on a script or stdin it cannot see. It reads `-c` strings, `eval` and command
substitution, and fails closed on input it cannot parse.

This is a second layer, not a sandbox: a determined command can evade a deny-list. The
boundary that holds is the dispatch rule that a reviewer gets no GitHub write credentials.
Standard library only, so the copy in a target repo needs nothing installed.
"""

from __future__ import annotations

import json
import sys

_BLANKS = set(" \t\r")
_SEPARATORS = set("();<>|&`\n")
_DQ_ESCAPES = set('"\\$`\n')
_WRAPPERS = {"env", "command", "exec", "nohup", "sudo", "xargs", "time", "nice"}
_SHELLS = {"sh", "bash", "zsh", "dash"}
_SHELL_VALUED = {"-o", "+o", "-O", "+O", "--rcfile", "--init-file"}
_GIT_VALUED = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}
_GH_VALUED = {"-R", "--repo", "--hostname"}
_GH_WRITES = {
    "pr": {"create", "comment", "review", "merge", "close", "reopen", "edit", "ready", "lock"},
    "issue": {"create", "comment", "edit", "close", "reopen", "delete", "lock", "transfer",
              "pin", "unpin", "develop"},
    "release": {"create", "delete", "edit", "upload"},
    "repo": {"create", "delete", "edit", "fork", "rename", "archive", "sync"},
    "workflow": {"run", "enable", "disable"},
    "run": {"rerun", "cancel", "delete"},
    "label": {"create", "edit", "delete", "clone"},
    "secret": {"set", "delete"},
    "variable": {"set", "delete"},
}
_GH_API_WRITE_FLAGS = {"-f", "-F", "--field", "--raw-field", "--input"}
_HTTP_WRITE_FLAGS = {"-d", "--data", "--data-raw", "--data-binary", "--data-urlencode",
                     "--json", "-F", "--form", "-T", "--upload-file", "--post-data",
                     "--post-file", "--body-data", "--body-file"}


def verdict(command: str) -> str | None:
    """Why the command is blocked, or None when it may run."""
    try:
        commands, nested = _commands(command)
    except ValueError as err:
        return f"cannot parse the command ({err}), so it is blocked"
    for words in commands:
        reason = _check(words)
        if reason:
            return reason
    for inner in nested:
        reason = verdict(inner)
        if reason:
            return reason
    return None


def _commands(command: str) -> tuple[list[list[str]], list[str]]:
    """Simple commands as words, and the double-quoted text that runs command substitution.

    A small shell lexer: quotes and backslashes join a word, a backslash-newline continues
    the line, an unquoted `#` at a word's start comments to the end of the line (not past
    it), and newlines, `;`, `&`, `|`, parentheses, backticks and redirects split commands.
    """
    commands: list[list[str]] = [[]]
    nested: list[str] = []
    word: list[str] | None = None
    i = 0
    while i < len(command):
        c = command[i]
        if c == "\\":
            i += 2
            if command[i - 1 : i] == "\n":
                continue
            word = (word or []) + [command[i - 1 : i]]
        elif c == "'":
            close = command.find("'", i + 1)
            if close < 0:
                raise ValueError("unterminated single quote")
            word, i = (word or []) + [command[i + 1 : close]], close + 1
        elif c == '"':
            text, i = _double_quoted(command, i + 1)
            if "$(" in text or "`" in text:
                nested.append(text)
            word = (word or []) + [text]
        elif c == "#" and word is None:
            close = command.find("\n", i)
            i = len(command) if close < 0 else close
        elif c in _BLANKS or c in _SEPARATORS:
            if word is not None:
                commands[-1].append("".join(word))
                word = None
            if c in _SEPARATORS:
                commands.append([])
            i += 1
        else:
            word, i = (word or []) + [c], i + 1
    if word is not None:
        commands[-1].append("".join(word))
    return [c for c in commands if c], nested


def _double_quoted(command: str, i: int) -> tuple[str, int]:
    """The text of a double-quoted string starting at `i`, and the index after its quote."""
    out: list[str] = []
    while i < len(command):
        c = command[i]
        if c == '"':
            return "".join(out), i + 1
        if c == "\\" and command[i + 1 : i + 2] in _DQ_ESCAPES:
            out.append(command[i + 1].replace("\n", ""))
            i += 2
            continue
        out.append(c)
        i += 1
    raise ValueError("unterminated double quote")


def _check(words: list[str]) -> str | None:
    words = _unwrap(words)
    if not words:
        return None
    program = words[0].rsplit("/", 1)[-1]
    if program in _SHELLS:
        return _check_shell(words)
    if program == "eval":
        return verdict(" ".join(words[1:]))
    if program == "git":
        return _check_git(words[1:])
    if program == "gh":
        return _check_gh(words[1:])
    if program in {"curl", "wget"}:
        return _check_http(words[1:])
    return None


def _unwrap(words: list[str]) -> list[str]:
    """Drop leading `VAR=value` assignments and wrappers such as `env` or `timeout 60`."""
    i = 0
    while i < len(words):
        word = words[i]
        if "=" in word and not word.startswith("-") and word.split("=", 1)[0].isidentifier():
            i += 1
        elif word in _WRAPPERS or word.startswith("-") and i and words[i - 1] in _WRAPPERS:
            i += 1
        elif word == "timeout":
            i += 2
        else:
            break
    return words[i:]


def _check_shell(words: list[str]) -> str | None:
    """Check a `-c` command string, `-c` bundled or not; a shell given none runs unseen code."""
    args, i, has_c = words[1:], 0, False
    while i < len(args) and args[i].startswith(("-", "+")) and args[i] not in {"-", "--"}:
        arg = args[i]
        has_c = has_c or (arg[0] == "-" and arg[1] != "-" and "c" in arg[1:])
        i += 2 if arg in _SHELL_VALUED else 1
    if not has_c:
        return f"`{words[0]}` without -c runs a script or stdin the guard cannot see"
    if i >= len(args):
        return f"`{words[0]} -c` with no command string, so it is blocked"
    return verdict(args[i])


def _subcommand(args: list[str], valued: set[str]) -> list[str]:
    """The arguments from the subcommand on, skipping global options and their values."""
    i = 0
    while i < len(args) and args[i].startswith("-"):
        i += 2 if args[i] in valued else 1
    return args[i:]


def _check_git(args: list[str]) -> str | None:
    rest = _subcommand(args, _GIT_VALUED)
    if rest and rest[0] in {"push", "send-email", "request-pull"}:
        return f"`git {rest[0]}` reaches a remote; reviewers are read-only"
    return None


def _check_gh(args: list[str]) -> str | None:
    rest = _subcommand(args, _GH_VALUED)
    if len(rest) >= 2 and rest[1] in _GH_WRITES.get(rest[0], set()):
        return f"`gh {rest[0]} {rest[1]}` writes to GitHub; reviewers are read-only"
    if rest and rest[0] == "api":
        return _check_gh_api(rest[1:])
    return None


def _check_gh_api(args: list[str]) -> str | None:
    method = _method(args, {"-X", "--method"})
    if method not in {None, "GET", "HEAD"}:
        return f"`gh api -X {method}` writes to GitHub; reviewers are read-only"
    if method is None and any(_flag(a) in _GH_API_WRITE_FLAGS for a in args):
        return "`gh api` with fields sends a POST; reviewers are read-only"
    return None


def _check_http(args: list[str]) -> str | None:
    method = _method(args, {"-X", "--request", "--method"})
    if method not in {None, "GET", "HEAD"}:
        return f"an HTTP {method} request; reviewers are read-only"
    if any(_flag(a) in _HTTP_WRITE_FLAGS for a in args):
        return "an HTTP request with a body; reviewers are read-only"
    return None


def _method(args: list[str], flags: set[str]) -> str | None:
    for i, arg in enumerate(args):
        name, eq, value = arg.partition("=")
        if name in flags:
            if eq:
                return value.upper()
            return args[i + 1].upper() if i + 1 < len(args) else "UNKNOWN"
        if arg.startswith("-X") and len(arg) > 2 and "-X" in flags:
            return arg[2:].upper()
    return None


def _flag(arg: str) -> str:
    return arg.partition("=")[0]


def main(stdin: str) -> tuple[int, str]:
    """Exit status and stderr for one hook call."""
    try:
        call = json.loads(stdin)
        command = call["tool_input"]["command"]
    except (json.JSONDecodeError, KeyError, TypeError) as err:
        return 2, f"read-only guard: unreadable hook input ({err!r}), so the call is blocked"
    if not isinstance(command, str):
        return 2, "read-only guard: the command is not a string, so the call is blocked"
    reason = verdict(command)
    return (2, f"read-only guard: {reason}") if reason else (0, "")


if __name__ == "__main__":
    status, message = main(sys.stdin.read())
    if message:
        print(message, file=sys.stderr)
    sys.exit(status)
