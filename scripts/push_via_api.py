# -*- coding: utf-8 -*-
"""git push 的备用通道：通过 GitHub Git Data API 提交（单次 commit）。

适用场景：企业代理 / 终端管控 / TLS 拦截导致 `git push` 失败，但
https://api.github.com 仍可访问。凭据取自本机 Git Credential Manager，
不会打印或写入磁盘。

用法（在仓库根目录执行）：
    python scripts/push_via_api.py --owner Zero-Grav --repo GHAccelerator \
        --branch main --message "docs: ..."

前置：远程 main 的 commit 必须已存在于本地对象库（本地从该仓库 clone 或
曾成功 fetch/push 过）。脚本用 `git diff <remote_sha> HEAD` 求变更集，
因此只上传差异文件，不重传整个仓库。
"""
from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, List

API = "https://api.github.com"


def git(*args: str) -> bytes:
    return subprocess.check_output(["git"] + list(args))


def credential_token() -> str:
    proc = subprocess.run(["git", "credential", "fill"],
                          input="protocol=https\nhost=github.com\n\n",
                          capture_output=True, text=True, check=True)
    for line in proc.stdout.splitlines():
        if line.startswith("password="):
            return line[len("password="):]
    raise RuntimeError("Git Credential Manager 中没有 github.com 凭据")


def api(token: str, method: str, url: str,
        payload: Dict[str, Any] = None) -> Dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") \
        if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "GHAccelerator-push-via-api",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise RuntimeError("GitHub API %s %s -> %d %s" %
                           (method, url, exc.code, detail)) from None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Push via GitHub Git Data API")
    parser.add_argument("--owner", required=True)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--branch", default="main")
    parser.add_argument("--message", required=True)
    parser.add_argument("--author-name", default="Zero-Grav")
    parser.add_argument("--author-email",
                        default="20814322+Zero-Grav@users.noreply.github.com")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--sync-local", action="store_true",
                        help="提交成功后把本地分支与 origin/<branch> 指到新 commit")
    args = parser.parse_args(argv)

    repo_url = "%s/repos/%s/%s" % (API, args.owner, args.repo)
    token = credential_token()

    ref = api(token, "GET", repo_url + "/git/ref/heads/" + args.branch)
    remote_sha = ref["object"]["sha"]
    try:
        git("cat-file", "-e", remote_sha + "^{commit}")
    except subprocess.CalledProcessError:
        print("远程 %s 的 commit 不在本地对象库，请先 git fetch", args.branch)
        return 2

    head = git("rev-parse", args.head).decode().strip()
    changed = git("diff", "--name-only", remote_sha, head).decode().strip().splitlines()
    deleted = git("diff", "--diff-filter=D", "--name-only", remote_sha, head) \
        .decode().strip().splitlines()
    changed = [p for p in changed if p and p not in deleted]
    if not changed and not deleted:
        print("无变更，远程已是最新")
        return 0

    base_commit = api(token, "GET", repo_url + "/git/commits/" + remote_sha)
    entries: List[Dict[str, Any]] = []
    for path in changed:
        content = git("show", "%s:%s" % (head, path))
        blob = api(token, "POST", repo_url + "/git/blobs", {
            "content": base64.b64encode(content).decode("ascii"),
            "encoding": "base64",
        })
        entries.append({"path": path, "mode": "100644", "type": "blob",
                        "sha": blob["sha"]})
        print("  + %-52s %d B" % (path, len(content)))
    for path in deleted:
        entries.append({"path": path, "mode": "100644", "type": "blob",
                        "sha": None})
        print("  - %s" % path)

    tree = api(token, "POST", repo_url + "/git/trees",
               {"base_tree": base_commit["tree"]["sha"], "tree": entries})
    commit = api(token, "POST", repo_url + "/git/commits", {
        "message": args.message,
        "tree": tree["sha"],
        "parents": [remote_sha],
        "author": {"name": args.author_name, "email": args.author_email},
    })
    # GET 用单数 /git/ref/heads/{branch}；PATCH 更新必须用复数且保留 heads/ 前缀
    api(token, "PATCH", repo_url + "/git/refs/heads/" + args.branch,
        {"sha": commit["sha"]})

    print("files_changed = %d (+%d / -%d)" % (len(entries), len(changed), len(deleted)))
    print("remote_sha_before = %s" % remote_sha)
    print("remote_sha_after  = %s" % commit["sha"])
    print("html_url = https://github.com/%s/%s/commit/%s" %
          (args.owner, args.repo, commit["sha"]))

    if args.sync_local:
        git("update-ref", "refs/heads/%s" % args.branch, commit["sha"])
        git("update-ref", "refs/remotes/origin/%s" % args.branch, commit["sha"])
        print("local branch synced to %s" % commit["sha"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
