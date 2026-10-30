import json
import unittest

from guard.readonly import main, verdict

ALLOWED = [
    "git fetch origin pull/7/head",
    "git -C /tmp/terminal log -L 10,20:src/host/VtIo.cpp",
    "git diff a..b -- crates/apps/rusty_mux",
    "git clone --filter=blob:none https://github.com/microsoft/terminal /tmp/t",
    "cargo test -p rusty_mux_server 2>&1 | tail -50",
    "gh pr view 7 --json headRefOid",
    "gh pr diff 7",
    "gh api repos/o/r/pulls/7/comments",
    "gh api -X GET repos/o/r/pulls/7",
    "curl -sL https://raw.githubusercontent.com/o/r/main/README.md",
    "grep -rn 'git push' docs/",
    "echo 'gh pr merge is not run here'",
    "python3 .github/scripts/changes.py check",
    "cargo test # then git push",
    "git log --format='%H # %s'",
    'git log --grep="fix #12"',
    "sh -c 'cargo test -p rusty_mux_server'",
    "cargo test \\\n  -p rusty_conhost",
]

BLOCKED = [
    ("git push origin HEAD", "git push"),
    ("git -C /src/r push --force", "git push"),
    ("cd x && git push", "git push"),
    ("cargo test;git push", "git push"),
    ("cargo test\ngit push", "git push"),
    ("GIT_TRACE=1 git push", "git push"),
    ("env -i git push", "git push"),
    ("timeout 60 git push", "git push"),
    ("bash -c 'git push origin main'", "git push"),
    ("eval git push", "git push"),
    ("(cd r && git push)", "git push"),
    ("/usr/bin/git push", "git push"),
    ("gh pr merge 7 --merge", "gh pr merge"),
    ("gh -R o/r pr review 7 --approve", "gh pr review"),
    ("gh pr comment 7 -b hi", "gh pr comment"),
    ("gh issue create -t x", "gh issue create"),
    ("gh workflow run ci.yml", "gh workflow run"),
    ("gh api -X POST repos/o/r/issues/7/comments -f body=x", "POST"),
    ("gh api --method=DELETE repos/o/r/git/refs/heads/x", "DELETE"),
    ("gh api repos/o/r/issues/7/comments -f body=x", "POST"),
    ("curl -X POST https://api.github.com/repos/o/r/issues", "POST"),
    ("curl -XPATCH https://api.github.com/x", "PATCH"),
    ("curl -d '{}' https://api.github.com/x", "body"),
    ("wget --post-data=x https://example.com", "body"),
    ("echo 'unbalanced", "cannot parse"),
    ('echo "unbalanced', "cannot parse"),
    # A comment ends at its newline, and a backslash-newline continues the command.
    ("git status # inspect\ngit push origin HEAD", "git push"),
    ("git \\\npush origin HEAD", "git push"),
    ("git status # a\n  # b\ngh pr merge 7", "gh pr merge"),
    # Shell options bundled with -c, or given values, still expose the command string.
    ("bash -lc 'git push origin HEAD'", "git push"),
    ("bash -lc 'gh pr merge 7'", "gh pr merge"),
    ("sh -ec 'gh pr review 7 --approve'", "gh pr review"),
    ("bash -o pipefail -c 'git push'", "git push"),
    ("bash --norc -c 'git push'", "git push"),
    ("bash -c", "no command string"),
    # A shell with no -c runs code the guard cannot see.
    ("echo 'git push' | bash", "cannot see"),
    ("bash probe.sh", "cannot see"),
    # Command substitution, quoted or not.
    ('echo "$(git push)"', "git push"),
    ("echo `gh pr merge 7`", "gh pr merge"),
    ("echo $(git push)", "git push"),
]


class VerdictTest(unittest.TestCase):
    def test_review_commands_run(self) -> None:
        for command in ALLOWED:
            self.assertIsNone(verdict(command), command)

    def test_remote_and_github_writes_are_blocked_with_a_reason(self) -> None:
        for command, why in BLOCKED:
            reason = verdict(command)
            self.assertIsNotNone(reason, command)
            self.assertIn(why, reason, command)


class HookTest(unittest.TestCase):
    def call(self, payload: object) -> tuple[int, str]:
        return main(json.dumps(payload))

    def test_an_allowed_command_exits_0_silently(self) -> None:
        self.assertEqual(self.call({"tool_name": "Bash", "tool_input": {"command": "ls"}}), (0, ""))

    def test_a_blocked_command_exits_2_with_the_reason(self) -> None:
        status, message = self.call({"tool_input": {"command": "git push"}})
        self.assertEqual(status, 2)
        self.assertIn("read-only guard: `git push`", message)

    def test_unreadable_input_fails_closed(self) -> None:
        for raw in ("not json", json.dumps({}), json.dumps({"tool_input": {"command": 3}}),
                    json.dumps([])):
            self.assertEqual(main(raw)[0], 2, raw)
