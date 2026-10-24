"""Scenario data shared by setup_contest.py (in the CMS container) and
driver.py (in the driver container or on any client host).

Every task is "print the sum of N numbers". Subtask categories:
  S: N <= 10, values <= 1000
  M: N <= 1000, values <= 1000
  L: N up to LARGE_N, values up to 1e9 (the sum overflows 32 bits)
  K: omi profile only, see below
Each group has a sample, a screening WA case and a screening TLE case
(codenames sN-00-sample, sN-01-scr-wa, sN-02-scr-tle), so two-phase
screening applies to every group.

The omi profile (OMI_TASKS) is the same sum task with category K, whose two
heavy testcases end with a "reps" line: the ac_cpu solution then spins that
many iterations, so a testcase costs a chosen amount of CPU.
"""

LARGE_N = 100_000
TIME_LIMIT = 1.0
TIME_LIMIT_OMI = 2.0
MEMORY_LIMIT_BYTES = 256 * 1024 * 1024
STATEMENT_BYTES = 300 * 1024
SCORE_MODE = "max_subtask"

LANGUAGES = {
    "cpp": "C++17 / g++",
    "py": "Python 3 / CPython",
    "java": "Java / JDK",
}

LANGUAGES_OMI = {"cpp": "C++20 / g++"}


def _omi_task(name: str, heavy_reps: int, chained: bool = False) -> dict:
    """Return an omi task: GroupMin, four K subtasks of 25 points.

    heavy_reps: spin iterations of the two heavy testcases of each subtask.
    chained: whether each subtask needs the one before it.

    """
    subtasks = [{"category": "K", "max_score": 25} for _ in range(4)]
    if chained:
        for index in (1, 2, 3):
            subtasks[index]["depends_on"] = [index - 1]
    return {"name": name, "score_type": "GroupMin", "heavy_reps": heavy_reps,
            "subtasks": subtasks}


# The reps are first estimates, 4 cycles per iteration (3 multiply chains):
# 600M take 0.65 s on a Zen 3 at about 3.5-4 GHz and 0.98 s at 2.45 GHz (an
# EPYC 7763 runner's base clock), 700M take 0.75 s and 1.14 s, and an SMT
# sibling running the same loop adds about 45%, so omicomm stays under the
# 2 s limit (1.66 s) at 2.45 GHz. Calibrate them with a GitHub Actions run
# (see the README). The OMI 2025 evaluations took 0.5-1.0 s. "omicomm" stands
# in for the Communication task of OMI 2025: a Batch task that costs a bit
# more CPU per testcase, with no FIFO or second sandbox.
OMI_TASKS = [
    _omi_task("omia", 600_000_000),
    _omi_task("omib", 600_000_000, chained=True),
    _omi_task("omic", 600_000_000),
    _omi_task("omicomm", 700_000_000),
]

TASKS = {
    "loada": [
        {"name": "suma", "score_type": "GroupMin", "subtasks": [
            {"category": "S", "max_score": 20},
            {"category": "M", "max_score": 30},
            {"category": "L", "max_score": 50, "depends_on": [0, 1]}]},
        {"name": "cadena", "score_type": "GroupMul", "subtasks": [
            {"category": "S", "max_score": 10},
            {"category": "M", "max_score": 20, "depends_on": [0]},
            {"category": "L", "max_score": 30, "depends_on": [1]},
            {"category": "L", "max_score": 40, "depends_on": [2]}]},
        {"name": "umbral", "score_type": "GroupThreshold", "subtasks": [
            {"category": "S", "max_score": 30},
            {"category": "L", "max_score": 70}]},
        {"name": "libre", "score_type": "GroupMin", "subtasks": [
            {"category": "S", "max_score": 25},
            {"category": "M", "max_score": 25},
            {"category": "L", "max_score": 50}]},
    ],
    "loadb": [
        {"name": "sumab", "score_type": "GroupMin", "subtasks": [
            {"category": "S", "max_score": 30},
            {"category": "M", "max_score": 30, "depends_on": [0]},
            {"category": "L", "max_score": 40, "depends_on": [1]}]},
        {"name": "umbralb", "score_type": "GroupThreshold", "subtasks": [
            {"category": "S", "max_score": 20},
            {"category": "M", "max_score": 30},
            {"category": "L", "max_score": 50, "depends_on": [0, 1]}]},
        {"name": "mulb", "score_type": "GroupMul", "subtasks": [
            {"category": "M", "max_score": 40},
            {"category": "L", "max_score": 60}]},
    ],
}

# Solution kinds: (file, language key, weight, categories it passes).
# "ce" does not compile: status "compilation failed", score 0.
SOLUTIONS = {
    "ac": ("ac.cpp", "cpp", 0.28, "SMLK"),
    "ac_cpu": ("ac_cpu.cpp", "cpp", 0.0, "SMLK"),
    "ac_py": ("ac.py", "py", 0.10, "SML"),
    "ac_java": ("ac.java.tmpl", "java", 0.04, "SML"),
    "wa_overflow": ("wa_overflow.cpp", "cpp", 0.14, "SM"),
    "tle": ("tle.cpp", "cpp", 0.14, "SM"),
    "wa_small": ("wa_small.cpp", "cpp", 0.10, "ML"),
    "re": ("re.cpp", "cpp", 0.08, "SM"),
    "ce": ("ce.cpp", "cpp", 0.07, ""),
    "wa_zero": ("wa_zero.cpp", "cpp", 0.05, ""),
}


# Share of the submissions of each kind in the omi profile; the kinds not
# listed (the Python and Java ones, plain ac) are never sent. The weights are
# first estimates: calibrate them against the OMI 2025 sandbox time per
# submission (about 15 s with the compilation).
OMI_WEIGHTS = {"ac_cpu": 0.30, "wa_overflow": 0.17, "tle": 0.15,
               "wa_small": 0.12, "re": 0.09, "ce": 0.08, "wa_zero": 0.09}

PROFILES = ("portable", "full", "omi")
# The profiles that use fork-only features: dependencies, two-phase, ranking
# groups.
FORK_PROFILES = ("full", "omi")

# Contests published to RWS per profile, with the scores path relative to
# the RWS base URL. Upstream ProxyService ranks one contest at the root.
RANKED = {
    "portable": {"loada": "scores"},
    "full": {"loada": "loada/scores", "loadb": "loadb/scores"},
    "omi": {"loada": "loada/scores"},
}


def _check_profile(profile: str) -> None:
    if profile not in PROFILES:
        raise ValueError("unknown profile %r (expected one of %s)"
                         % (profile, ", ".join(PROFILES)))


def two_phase(profile: str) -> bool:
    """Return whether two-phase evaluation is on in the profile."""
    _check_profile(profile)
    return profile in FORK_PROFILES


def contest_tasks(contest: str, profile: str) -> list[dict]:
    """Return the task specs of the contest in the profile."""
    _check_profile(profile)
    if profile == "omi":  # one contest; loadb stays empty
        return OMI_TASKS if contest == "loada" else []
    return TASKS[contest]


def weights(profile: str) -> dict[str, float]:
    """Return the share of the submissions of each solution kind."""
    _check_profile(profile)
    if profile == "omi":
        return {kind: OMI_WEIGHTS.get(kind, 0.0) for kind in SOLUTIONS}
    return {kind: solution[2] for kind, solution in SOLUTIONS.items()}


def languages(profile: str) -> dict[str, str]:
    """Return the CMS language name of each language key."""
    _check_profile(profile)
    return LANGUAGES_OMI if profile == "omi" else LANGUAGES


def time_limit(profile: str) -> float:
    """Return the time limit in seconds of every task of the profile."""
    _check_profile(profile)
    return TIME_LIMIT_OMI if profile == "omi" else TIME_LIMIT


def task_spec(task_name: str, profile: str) -> dict:
    """Return the task's spec as the profile uses it.

    The portable profile drops every depends_on, since upstream CMS has
    no subtask dependencies.

    """
    _check_profile(profile)
    for tasks in (*TASKS.values(), OMI_TASKS):
        for spec in tasks:
            if spec["name"] == task_name:
                if profile in FORK_PROFILES:
                    return spec
                return dict(spec, subtasks=[
                    {k: v for k, v in sub.items() if k != "depends_on"}
                    for sub in spec["subtasks"]])
    raise KeyError(task_name)


def expected_score(task_name: str, kind: str, profile: str) -> float:
    """Return the score the solution kind must get on the task."""
    spec = task_spec(task_name, profile)
    passes = SOLUTIONS[kind][3]
    ok: list[bool] = []
    for sub in spec["subtasks"]:
        own = sub["category"] in passes
        deps = all(ok[d] for d in sub.get("depends_on", []))
        ok.append(own and deps)
    return float(sum(sub["max_score"]
                     for sub, good in zip(spec["subtasks"], ok) if good))
