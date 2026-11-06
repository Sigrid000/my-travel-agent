from pathlib import Path

from app.observability.trace import TraceRecorder


def test_log_final_writes_full_content_and_sidecar(tmp_path: Path) -> None:
    long = "行程正文\n" + ("沙坡头 " * 200)
    tr = TraceRecorder(session_id="t1", trace_dir=str(tmp_path))
    side = tr.log_final(long)

    assert side.exists()
    assert side.read_text(encoding="utf-8") == long

    rows = [line for line in tr.path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 1
    import json

    ev = json.loads(rows[0])
    assert ev["kind"] == "final"
    assert ev["content"] == long
    assert ev["content_chars"] == len(long)
    assert "preview" not in ev

    summary = tr.summary()
    assert summary["final_path"].endswith(".final.md")
