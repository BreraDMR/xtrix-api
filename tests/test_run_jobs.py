import importlib.util
from pathlib import Path


def _load():
    spec = importlib.util.spec_from_file_location("run_jobs", Path(__file__).resolve().parents[1] / "scripts" / "run_jobs.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_one_failing_job_does_not_stop_the_rest(monkeypatch):
    jobs = _load()
    ran = []

    def boom():
        raise RuntimeError("smtp down")

    monkeypatch.setattr(jobs, "JOBS", {"a": lambda: ran.append("a"), "b": boom, "c": lambda: ran.append("c")})
    assert jobs.run(["a", "b", "c"]) is False
    assert ran == ["a", "c"]


def test_every_job_runs_on_an_empty_database():
    jobs = _load()
    assert jobs.run(list(jobs.JOBS)) is True
