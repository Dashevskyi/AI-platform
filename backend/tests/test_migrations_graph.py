from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def _load_script_directory() -> ScriptDirectory:
    backend_dir = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    return ScriptDirectory.from_config(cfg)


def test_alembic_has_single_head():
    script = _load_script_directory()
    heads = script.get_heads()
    assert len(heads) == 1, f"Expected exactly one Alembic head, got: {heads}"


def test_alembic_history_is_linear():
    script = _load_script_directory()

    branching_points = []
    for revision in script.walk_revisions():
        next_revisions = revision.nextrev
        if isinstance(next_revisions, str):
            next_count = 1
        else:
            next_count = len(tuple(next_revisions))
        if next_count > 1:
            branching_points.append(revision.revision)

    assert not branching_points, f"Alembic history contains branches at: {branching_points}"
