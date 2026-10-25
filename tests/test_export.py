import json

import httpx
import pytest

from orderline import export


def api(handler_map):
    calls = []

    def handler(request):
        calls.append(request)
        key = (request.method, request.url.path)
        reply = handler_map[key]
        body = reply(request) if callable(reply) else reply
        return httpx.Response(200, json=body)

    return httpx.Client(transport=httpx.MockTransport(handler)), calls


COLUMNS = {
    "columns": [
        {"name": "n", "type_name": "LONG"},
        {"name": "share", "type_name": "DECIMAL"},
        {"name": "ok", "type_name": "BOOLEAN"},
        {"name": "day", "type_name": "DATE"},
    ]
}


def test_picks_the_running_warehouse_and_types_the_result():
    client, calls = api(
        {
            ("GET", "/api/2.0/sql/warehouses"): {
                "warehouses": [{"id": "w-off", "state": "STOPPED"}, {"id": "w-on", "state": "RUNNING"}]
            },
            ("POST", "/api/2.0/sql/statements"): {
                "statement_id": "s1",
                "status": {"state": "SUCCEEDED"},
                "manifest": {"schema": COLUMNS},
                "result": {"data_array": [["3", "0.25", "true", "2026-10-24"], [None, None, "false", None]]},
            },
        }
    )
    warehouse = export.SqlWarehouse("dbc-1.cloud.databricks.com", "t", client=client)
    rows = warehouse.query("select 1")
    assert warehouse.warehouse_id == "w-on"
    assert rows == [
        {"n": 3, "share": 0.25, "ok": True, "day": "2026-10-24"},
        {"n": None, "share": None, "ok": False, "day": None},
    ]
    sent = json.loads(calls[1].content)
    assert sent["warehouse_id"] == "w-on" and sent["disposition"] == "INLINE"


def test_waits_for_a_cold_warehouse_and_follows_chunks():
    states = iter(["RUNNING", "SUCCEEDED"])

    def poll(_):
        state = next(states)
        body = {"statement_id": "s1", "status": {"state": state}}
        if state == "SUCCEEDED":
            body |= {
                "manifest": {"schema": {"columns": [{"name": "n", "type_name": "INT"}]}},
                "result": {
                    "data_array": [["1"]],
                    "next_chunk_internal_link": "/api/2.0/sql/statements/s1/result/chunks/1",
                },
            }
        return body

    client, _ = api(
        {
            ("POST", "/api/2.0/sql/statements"): {"statement_id": "s1", "status": {"state": "PENDING"}},
            ("GET", "/api/2.0/sql/statements/s1"): poll,
            ("GET", "/api/2.0/sql/statements/s1/result/chunks/1"): {"data_array": [["2"]]},
        }
    )
    warehouse = export.SqlWarehouse("https://h", "t", warehouse_id="w", client=client, poll_s=0)
    assert warehouse.query("select n") == [{"n": 1}, {"n": 2}]


def test_a_failed_statement_says_why_and_shows_the_query():
    client, _ = api(
        {
            ("POST", "/api/2.0/sql/statements"): {
                "statement_id": "s1",
                "status": {"state": "FAILED", "error": {"message": "TABLE_OR_VIEW_NOT_FOUND: ml.late_risk"}},
            },
        }
    )
    warehouse = export.SqlWarehouse("h", "t", warehouse_id="w", client=client)
    with pytest.raises(
        export.QueryFailed, match=r"TABLE_OR_VIEW_NOT_FOUND(.|\n)*select \* from ml.late_risk"
    ):
        warehouse.query("select * from ml.late_risk")


class Canned:
    """Answers each export query from a dict keyed by a word in the query."""

    def __init__(self, answers):
        self.answers = answers

    def query(self, sql):
        for name, rows in self.answers.items():
            if name in sql:
                return rows
        return []


def test_export_writes_one_file_and_refuses_an_empty_page(tmp_path):
    answers = {
        "count_if(status IN": [{"orders": 10}],
        "daily_delivery": [{"day": "2026-10-24", "orders": 5}],
        "training_runs": [
            {"model_version": "1", "checks": '{"carrier": "ok"}', "importances": '{"distance_km": 0.1}'}
        ],
    }
    out = tmp_path / "data" / "site.json"
    data = export.export(Canned(answers), "orderline", out)
    assert json.loads(out.read_text())["summary"] == {"orders": 10}
    assert data["runs"][0]["importances"] == {"distance_km": 0.1}

    with pytest.raises(export.QueryFailed, match="empty page"):
        export.export(Canned({}), "orderline", tmp_path / "again.json")


def test_every_query_names_the_catalog():
    for name, sql in export.queries("shopcat").items():
        assert "shopcat." in sql, name
