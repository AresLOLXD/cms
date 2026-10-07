"""Scenario data shared by setup_contest.py (in the CMS container) and
driver.py (in the driver container or on any client host).

Every task is "print the sum of N numbers". Subtask categories:
  S: N <= 10, values <= 1000
  M: N <= 1000, values <= 1000
  L: N up to LARGE_N, values up to 1e9 (the sum overflows 32 bits)
Each group has a sample, a screening WA case and a screening TLE case
(codenames sN-00-sample, sN-01-scr-wa, sN-02-scr-tle), so two-phase
screening applies to every group.
"""

LARGE_N = 100_000
TIME_LIMIT = 1.0
MEMORY_LIMIT_BYTES = 256 * 1024 * 1024
STATEMENT_BYTES = 300 * 1024
SCORE_MODE = "max_subtask"

LANGUAGES = {
    "cpp": "C++17 / g++",
    "py": "Python 3 / CPython",
    "java": "Java / JDK",
}

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
    "ac": ("ac.cpp", "cpp", 0.28, "SML"),
    "ac_py": ("ac.py", "py", 0.10, "SML"),
    "ac_java": ("ac.java.tmpl", "java", 0.04, "SML"),
    "wa_overflow": ("wa_overflow.cpp", "cpp", 0.14, "SM"),
    "tle": ("tle.cpp", "cpp", 0.14, "SM"),
    "wa_small": ("wa_small.cpp", "cpp", 0.10, "ML"),
    "re": ("re.cpp", "cpp", 0.08, "SM"),
    "ce": ("ce.cpp", "cpp", 0.07, ""),
    "wa_zero": ("wa_zero.cpp", "cpp", 0.05, ""),
}


PROFILES = ("portable", "full")

# Contests published to RWS per profile, with the scores path relative to
# the RWS base URL. Upstream ProxyService ranks one contest at the root.
RANKED = {
    "portable": {"loada": "scores"},
    "full": {"loada": "loada/scores", "loadb": "loadb/scores"},
}


def _check_profile(profile: str) -> None:
    if profile not in PROFILES:
        raise ValueError("unknown profile %r (expected one of %s)"
                         % (profile, ", ".join(PROFILES)))


def two_phase(profile: str) -> bool:
    """Return whether two-phase evaluation is on in the profile."""
    _check_profile(profile)
    return profile == "full"


def task_spec(task_name: str, profile: str) -> dict:
    """Return the task's spec as the profile uses it.

    The portable profile drops every depends_on, since upstream CMS has
    no subtask dependencies.

    """
    _check_profile(profile)
    for tasks in TASKS.values():
        for spec in tasks:
            if spec["name"] == task_name:
                if profile == "full":
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
