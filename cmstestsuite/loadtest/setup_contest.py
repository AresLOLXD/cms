#!/usr/bin/env python3
"""Create the two load-test contests inside the CMS container.

Runs with the CMS venv python (inside the "cms" container, or on any host
whose cms.toml points to the target database). It creates, from
scenario.py:

- ranking groups "loada" and "loadb" (one public scoreboard per contest);
- contests "loada" and "loadb", active (served by CWS -c ALL), with the
  start/stop given on the command line;
- the tasks, one dataset each, with testcases named "sN-nn-tag" so that
  two-phase screening and subtask dependencies both apply;
- users with bcrypt passwords (like AWS/CMS-Loader imports) and their
  participations.

It writes users.json (usernames, plaintext passwords, contest) for the
driver. It refuses to run if a contest named "loada" already exists.

"""

import argparse
import json
import random
import sys
from datetime import datetime, timedelta, timezone

from cms.db import (Contest, Dataset, Group, Participation, RankingGroup,
                    SessionGen, Statement, Task, Testcase, User)
from cms.db.filecacher import FileCacher
from cmscommon.crypto import hash_password

sys.path.insert(0, "/loadtest")
import scenario  # noqa: E402


def make_input(rng: random.Random, n: int, max_value: int) -> tuple[bytes, bytes]:
    values = [rng.randint(1, max_value) for _ in range(n)]
    text = "%d\n%s\n" % (n, " ".join(map(str, values)))
    return text.encode(), ("%d\n" % sum(values)).encode()


def group_cases(category: str) -> list[tuple[str, int, int]]:
    """Return (tag, n, max_value) for every testcase of a group."""
    small, medium, large = 1000, 1000, 10 ** 9
    if category == "S":
        return [("sample", 3, small), ("scr-wa", 10, small),
                ("scr-tle", 10, small), ("t", 7, small), ("t", 9, small),
                ("t", 10, small)]
    if category == "M":
        return [("sample", 20, medium), ("scr-wa", 1000, medium),
                ("scr-tle", 1000, medium), ("t", 500, medium),
                ("t", 700, medium), ("t", 900, medium), ("t", 1000, medium),
                ("t", 999, medium)]
    if category == "L":
        return [("sample", 20, small), ("scr-wa", 1000, large),
                ("scr-tle", scenario.LARGE_N, large)] + [
            ("t", scenario.LARGE_N // 2 + i * scenario.LARGE_N // 14, large)
            for i in range(7)]
    raise ValueError(category)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-in", type=float, required=True,
                        help="seconds from now to the contest start")
    parser.add_argument("--duration", type=float, required=True,
                        help="contest length in seconds")
    parser.add_argument("--users-a", type=int, required=True)
    parser.add_argument("--users-b", type=int, required=True)
    parser.add_argument("--out", default="/loadtest/out/users.json")
    args = parser.parse_args()

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    start = now + timedelta(seconds=args.start_in)
    stop = start + timedelta(seconds=args.duration)
    rng = random.Random(20261010)
    file_cacher = FileCacher()
    statement_digest = file_cacher.put_file_content(
        b"%PDF-1.4\n" + rng.randbytes(scenario.STATEMENT_BYTES),
        "load test statement")

    users_out = []
    with SessionGen() as session:
        if session.query(Contest).filter(Contest.name == "loada").first():
            print("Contest loada already exists; reset the DB first.")
            return 1
        digests: dict[tuple, tuple[str, str]] = {}
        for contest_name, user_count in (("loada", args.users_a),
                                         ("loadb", args.users_b)):
            ranking_group = RankingGroup(
                name=contest_name, description="Ranking %s" % contest_name)
            session.add(ranking_group)
            contest = Contest(
                name=contest_name, description="Load test %s" % contest_name,
                languages=list(scenario.LANGUAGES.values()),
                active=True, ranking_group=ranking_group,
                allow_questions=True, allow_user_tests=False)
            group = Group(name="default", start=start, stop=stop)
            contest.groups.append(group)
            contest.main_group = group
            session.add(contest)

            for num, spec in enumerate(scenario.TASKS[contest_name]):
                task = Task(
                    name=spec["name"], title=spec["name"].capitalize(),
                    num=num, contest=contest,
                    submission_format=["%s.%%l" % spec["name"]],
                    primary_statements=["es"],
                    feedback_level="full",
                    score_mode=scenario.SCORE_MODE,
                    score_precision=0)
                session.add(task)
                task.statements["es"] = Statement(
                    language="es", digest=statement_digest)
                parameters = []
                for index, sub in enumerate(spec["subtasks"]):
                    entry = {"max_score": sub["max_score"],
                             "testcases": "^s%d-" % (index + 1)}
                    if spec["score_type"] == "GroupThreshold":
                        entry["threshold"] = 1.0
                    if sub.get("depends_on"):
                        entry["depends_on"] = sub["depends_on"]
                    parameters.append(entry)
                dataset = Dataset(
                    task=task, description="Default",
                    time_limit=scenario.TIME_LIMIT,
                    memory_limit=scenario.MEMORY_LIMIT_BYTES,
                    task_type="Batch",
                    task_type_parameters=["alone", ["", ""], "diff"],
                    score_type=spec["score_type"],
                    score_type_parameters=parameters)
                session.add(dataset)
                task.active_dataset = dataset
                for index, sub in enumerate(spec["subtasks"]):
                    for case_num, (tag, n, max_value) in enumerate(
                            group_cases(sub["category"])):
                        key = (sub["category"], case_num)
                        if key not in digests:
                            data_in, data_out = make_input(rng, n, max_value)
                            digests[key] = (
                                file_cacher.put_file_content(data_in, "in"),
                                file_cacher.put_file_content(data_out, "out"))
                        codename = "s%d-%02d-%s" % (index + 1, case_num, tag)
                        dataset.testcases[codename] = Testcase(
                            codename=codename, public=True,
                            input=digests[key][0], output=digests[key][1])

            for i in range(1, user_count + 1):
                username = "%s%03d" % (contest_name[-1], i)
                password = "pw-%s-%d" % (username, rng.randint(10 ** 5, 10 ** 6))
                user = User(username=username, first_name="User",
                            last_name=username,
                            password=hash_password(password, "bcrypt"))
                session.add(user)
                session.add(Participation(user=user, contest=contest,
                                          group=group))
                users_out.append({"username": username, "password": password,
                                  "contest": contest_name})
        session.commit()

    with open(args.out, "w") as f:
        json.dump({"start": start.replace(tzinfo=timezone.utc).timestamp(),
                   "stop": stop.replace(tzinfo=timezone.utc).timestamp(),
                   "users": users_out}, f)
    print("Created contests; start %s UTC, stop %s UTC, %d users."
          % (start, stop, len(users_out)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
