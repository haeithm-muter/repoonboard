"""Clone the pinned repositories and build their ground truth.

Run this once per pin change:

    python eval/fetch.py [--work DIR] [--token GITHUB_TOKEN]

It writes `eval/ground_truth.json`, which `eval/run.py` scores against. The
ground truth is kept in its own generated file rather than written back into
`repos.toml`, so the hand-pinned facts and the derived ones never sit in the
same file pretending to be the same kind of thing.

Nothing here is clever. It clones at the pinned commit, reads CONTRIBUTING,
counts commits per file, pulls beginner issues from the GitHub API, and hands
all of it to `repoonboard.evaluation`, which does the actual work and is
tested. An unauthenticated token works but the search API allows only ten
requests a minute; pass --token for a comfortable run.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from repoonboard.discovery import discover
from repoonboard.evaluation import build_ground_truth

REPOS = ROOT / "eval" / "repos.toml"
OUTPUT = ROOT / "eval" / "ground_truth.json"


def run_git(args: list[str], cwd: Path | None = None, timeout: int = 1800) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()[:300]}")
    return result.stdout


def ensure_clone(url: str, commit: str, target: Path) -> None:
    """Clone with full history — churn needs it — and check out the pin."""
    if not (target / ".git").is_dir():
        target.parent.mkdir(parents=True, exist_ok=True)
        print(f"  cloning {url} (full history) ...", flush=True)
        run_git(["clone", "--quiet", url, str(target)])

    have = run_git(["cat-file", "-t", commit], cwd=target).strip() if commit else ""
    if have != "commit":
        run_git(["fetch", "--quiet", "origin", commit], cwd=target)
    run_git(["checkout", "--quiet", "--detach", commit], cwd=target)


def commit_counts(target: Path, commit: str) -> dict[str, int]:
    """Commits touching each path across the whole history up to the pin."""
    output = run_git(
        ["log", commit, "--name-only", "--pretty=format:", "--no-merges"], cwd=target
    )
    counts: dict[str, int] = {}
    for line in output.splitlines():
        name = line.strip()
        if name:
            counts[name] = counts.get(name, 0) + 1
    return counts


def read_contributing(target: Path, relative: str | None) -> str:
    if not relative:
        return ""
    path = target / relative
    if not path.is_file():
        print(f"  ! CONTRIBUTING not found at {relative}")
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def api_get(url: str, token: str | None) -> dict | None:
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    request.add_header("User-Agent", "repoonboard-eval")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        print(f"  ! {error.code} for {url}")
        return None
    except (urllib.error.URLError, TimeoutError) as error:
        print(f"  ! network error for {url}: {error}")
        return None


def issue_texts(slug: str, token: str | None, pause: float, comment_issues: int) -> list[str]:
    """Titles and bodies of every issue labelled "good first issue".

    Comments are fetched only when `comment_issues` is positive, because they
    use the core API, whose unauthenticated budget of sixty requests an hour
    cannot cover three repositories. Letting the fetch silently degrade when
    that budget runs out would make the ground truth depend on how recently
    the script was last run, which is no basis for a published number — so
    the richer mode is opt-in and recorded in the output.
    """
    query = urllib.parse.quote(f'repo:{slug} label:"good first issue"', safe=":+")
    texts: list[str] = []
    seen_numbers: list[int] = []

    for page in range(1, 4):  # 300 issues is more than enough
        payload = api_get(
            f"https://api.github.com/search/issues?q={query}&per_page=100&page={page}", token
        )
        if not payload or not payload.get("items"):
            break
        for item in payload["items"]:
            texts.append(item.get("title") or "")
            texts.append(item.get("body") or "")
            if item.get("comments"):
                seen_numbers.append(item["number"])
        if len(payload["items"]) < 100:
            break
        time.sleep(pause)

    owner, _, name = slug.partition("/")
    for number in sorted(seen_numbers)[:comment_issues]:
        comments = api_get(
            f"https://api.github.com/repos/{owner}/{name}/issues/{number}/comments?per_page=100",
            token,
        )
        if comments:
            texts.extend(comment.get("body") or "" for comment in comments)
        time.sleep(pause)

    return texts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", default=str(ROOT / ".eval-cache"))
    parser.add_argument("--token", default=None, help="GitHub token; raises the API rate limit.")
    parser.add_argument("--pause", type=float, default=2.0)
    parser.add_argument(
        "--comments",
        type=int,
        default=0,
        help=(
            "Also read comments from this many issues per repository. Needs a "
            "token to be worth using; 0 keeps the run inside the unauthenticated "
            "budget and therefore reproducible."
        ),
    )
    args = parser.parse_args()

    work = Path(args.work)
    config = tomllib.loads(REPOS.read_text(encoding="utf-8"))

    results = []
    for entry in config["repo"]:
        name = entry["name"]
        slug = entry["url"].removeprefix("https://github.com/")
        print(f"\n{name} ({slug})")

        target = work / name
        ensure_clone(entry["url"], entry["commit"], target)

        files = discover(target)
        known = frozenset(item.posix for item in files if not item.is_test)
        print(f"  discovered {len(files)} files, {len(known)} non-test source files")

        counts = commit_counts(target, entry["commit"])
        contributing = read_contributing(target, entry.get("contributing_path"))
        issues = (
            issue_texts(slug, args.token, args.pause, args.comments)
            if entry.get("good_first_issue_count", 0)
            else []
        )

        truth = build_ground_truth(
            contributing_text=contributing,
            commit_counts=counts,
            issue_texts=issues,
            known=known,
        )

        per_source = {source.value: sorted(paths) for source, paths in truth.by_source.items()}
        print(
            "  ground truth: "
            + ", ".join(f"{key}={len(value)}" for key, value in sorted(per_source.items()))
            + f"  union={len(truth.paths)}"
        )

        results.append(
            {
                "name": name,
                "slug": slug,
                "commit": entry["commit"],
                "language": entry["language"],
                "source_files": len(known),
                "discovered_files": len(files),
                "ground_truth": sorted(truth.paths),
                "by_source": per_source,
                "contributing_sources": [s.value for s in truth.contributing_sources()],
                "issue_comments_read": args.comments,
            }
        )

    OUTPUT.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(f"\nwrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
