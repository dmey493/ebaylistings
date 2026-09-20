import json

from ebaylister import cli
from ebaylister import pipeline as pl
from ebaylister.storage import JobStore


def _run(settings, argv, monkeypatch, capsys):
    monkeypatch.setattr(cli, "load_settings", lambda path: settings)
    rc = cli.main(argv)
    out = capsys.readouterr()
    return rc, out.out, out.err


def test_job_commands_round_trip(settings, photo, identified, category, prices, draft, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(pl.taxonomy, "pick_category", lambda client, q: category)
    monkeypatch.setattr(pl.browse, "price_comps", lambda client, q, cat=None: prices)
    monkeypatch.setattr(pl.EbayClient, "__init__", lambda self, s: None)  # never touch the network

    rc, out, _ = _run(settings, ["job", "new", str(photo), "-n", "no box"], monkeypatch, capsys)
    assert rc == 0
    job_id = out.strip()

    rc, out, _ = _run(settings, ["job", "show", job_id], monkeypatch, capsys)
    assert rc == 0 and json.loads(out)["note"] == "no box"

    ident = tmp_path / "identified.json"
    ident.write_text(identified.model_dump_json())
    rc, out, _ = _run(settings, ["job", "identified", job_id, str(ident)], monkeypatch, capsys)
    assert rc == 0
    ctx = json.loads(out)
    assert ctx["category"]["required_aspects"] == ["Brand", "Model"]
    assert ctx["comps"]["median"] == 160

    # a draft missing a required aspect is rejected and the job records why
    bad = draft.model_copy(update={"aspects": []})
    bad_file = tmp_path / "bad.json"
    bad_file.write_text(bad.model_dump_json())
    rc, _, err = _run(settings, ["job", "draft", job_id, str(bad_file)], monkeypatch, capsys)
    assert rc == 1 and "Brand" in err
    assert JobStore(settings).load(job_id).status == "failed"

    good = tmp_path / "draft.json"
    good.write_text(draft.model_dump_json())
    rc, out, _ = _run(settings, ["job", "draft", job_id, str(good)], monkeypatch, capsys)
    assert rc == 0 and "awaiting_review" in out
    assert JobStore(settings).load(job_id).status == "awaiting_review"


def test_schema_command_prints_json_schema(settings, monkeypatch, capsys):
    rc, out, _ = _run(settings, ["schema", "identified"], monkeypatch, capsys)
    assert rc == 0
    schema = json.loads(out)
    assert "condition" in schema["properties"] and "USED_GOOD" in json.dumps(schema)
