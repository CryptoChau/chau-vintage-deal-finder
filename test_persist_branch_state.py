# -*- coding: utf-8 -*-
"""The deal-finder state push must stay on its own branch.

A rejected push used to `git reset --hard origin/main` and push again.
On a branch run that would move this PR onto main's tree and could publish
state there. Main's commit must stay untouched.
"""
import os
import shutil
import subprocess
import tempfile
import unittest


ROOT = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(ROOT, "scripts", "persist_branch_state.sh")
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "deal-finder.yml")


def git(cwd, *args, check=True):
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=check,
        text=True,
        capture_output=True,
    )


class PersistBranchStateTests(unittest.TestCase):
    def test_deal_finder_workflow_does_not_reset_to_main(self):
        with open(WORKFLOW, encoding="utf-8") as fh:
            workflow = fh.read()
        with open(SCRIPT, encoding="utf-8") as fh:
            script = fh.read()
        self.assertNotIn("origin/main", workflow)
        self.assertNotIn("reset --hard origin/main", workflow)
        self.assertNotIn("fetch origin main", workflow)
        self.assertIn("scripts/persist_branch_state.sh", workflow)
        self.assertNotIn("origin/main", script)
        self.assertNotIn("fetch origin main", script)
        self.assertIn('git push origin "HEAD:${BRANCH}"', script)
        self.assertIn('git reset --hard "origin/${BRANCH}"', script)

    def test_psa_workflow_persists_on_its_branch_and_keeps_schedule_webhook(self):
        path = os.path.join(ROOT, ".github", "workflows", "psa-finder.yml")
        with open(path, encoding="utf-8") as fh:
            workflow = fh.read()
        self.assertNotIn("origin/main", workflow)
        self.assertNotIn("reset --hard origin/main", workflow)
        self.assertNotIn("fetch origin main", workflow)
        self.assertNotIn("git push", workflow)
        self.assertIn("scripts/persist_branch_state.sh psa_state.json", workflow)
        self.assertIn("inputs.dry_run != true", workflow)
        self.assertIn("post_to_vintage:", workflow)
        self.assertIn("default: false", workflow)
        self.assertIn("github.event_name == 'workflow_dispatch' && inputs.post_to_vintage == true", workflow)
        self.assertIn("secrets.DISCORD_WEBHOOK_HOLO", workflow)
        self.assertIn("secrets.DISCORD_WEBHOOK_PSA", workflow)
        self.assertIn('cron: "21 */2 * * *"', workflow)

    def test_rejected_push_retries_feature_branch_and_leaves_main(self):
        tmp = tempfile.mkdtemp(prefix="persist-state-")
        try:
            remote = os.path.join(tmp, "remote.git")
            local = os.path.join(tmp, "local")
            other = os.path.join(tmp, "other")
            subprocess.check_call(["git", "init", "--bare", remote])
            self._commit_base(local, remote)
            main_sha = git(remote, "rev-parse", "main").stdout.strip()

            git(local, "checkout", "-b", "feature")
            with open(os.path.join(local, "feature-marker.txt"), "w", encoding="utf-8") as fh:
                fh.write("keep-on-feature\n")
            git(local, "add", "feature-marker.txt")
            git(local, "commit", "-m", "feature work")
            git(local, "push", "-u", "origin", "feature")

            subprocess.check_call(["git", "clone", remote, other])
            git(other, "config", "user.email", "test@example.com")
            git(other, "config", "user.name", "test")
            git(other, "checkout", "feature")
            with open(os.path.join(other, "remote-move.txt"), "w", encoding="utf-8") as fh:
                fh.write("moved\n")
            git(other, "add", "remote-move.txt")
            git(other, "commit", "-m", "remote moved feature")
            git(other, "push", "origin", "feature")

            with open(os.path.join(local, "deal_state.json"), "w", encoding="utf-8") as fh:
                fh.write('{"seen":{"feature-only":"2026-11-08"}}\n')
            env = os.environ.copy()
            env["GITHUB_REF_NAME"] = "feature"
            proc = subprocess.run(
                ["sh", SCRIPT, "deal_state.json", "Update state [skip ci]"],
                cwd=local,
                env=env,
                text=True,
                capture_output=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(git(remote, "rev-parse", "main").stdout.strip(), main_sha)
            feature_tip = git(remote, "rev-parse", "feature").stdout.strip()
            self.assertNotEqual(feature_tip, main_sha)
            shown = git(remote, "show", "feature:feature-marker.txt").stdout
            self.assertIn("keep-on-feature", shown)
            state = git(remote, "show", "feature:deal_state.json").stdout
            self.assertIn("feature-only", state)
            self.assertNotIn("reset --hard origin/main", proc.stderr)
            self.assertNotIn("fetch origin main", proc.stderr)
            parents = git(remote, "rev-parse", "feature^").stdout.strip()
            self.assertNotEqual(parents, main_sha)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _commit_base(self, local, remote):
        subprocess.check_call(["git", "init", local])
        git(local, "config", "user.email", "test@example.com")
        git(local, "config", "user.name", "test")
        with open(os.path.join(local, "main-only.txt"), "w", encoding="utf-8") as fh:
            fh.write("main-only\n")
        git(local, "add", "main-only.txt")
        git(local, "commit", "-m", "main")
        git(local, "branch", "-M", "main")
        git(local, "remote", "add", "origin", remote)
        git(local, "push", "-u", "origin", "main")


if __name__ == "__main__":
    unittest.main()
