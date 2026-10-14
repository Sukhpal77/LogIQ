from collector import Queue


def test_tail_cursor_survives_restart_and_waits_for_newline(tmp_path):
    source = tmp_path / "app.jsonl"
    source.write_bytes(b'{"level":"error","message":"first"}\n{"level":"info","message":"second"}')
    queue = Queue(str(tmp_path / "queue.sqlite3"))
    assert queue.tail(str(source), "checkout") == 1
    reopened = Queue(queue.path)
    assert reopened.tail(str(source), "checkout") == 0
    with source.open("ab") as file:
        file.write(b"\n")
    assert reopened.tail(str(source), "checkout") == 1
    assert reopened.counts() == {0: 2}


def test_tail_backpressure_does_not_advance_cursor(tmp_path):
    source = tmp_path / "app.jsonl"
    source.write_text('{"level":"info","message":"first"}\n{"level":"info","message":"second"}\n')
    queue = Queue(str(tmp_path / "queue.sqlite3"), max_events=1)
    assert queue.tail(str(source), "checkout") == 1
    assert queue.tail(str(source), "checkout") == 0
    queue.finish([queue.batch("logs", 1)[0][0]])
    assert queue.tail(str(source), "checkout") == 1
