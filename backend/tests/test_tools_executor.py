import json

from app.services.tools.executor import _build_records_output


def test_build_records_output_returns_structured_json_payload():
    output = _build_records_output(
        rows=[
            {
                "id": 755,
                "name": "Харцизская 155",
                "address": "Харцизская 155",
                "ip": "172.16.52.96",
            }
        ],
        limit=25,
        result_columns=["id", "name", "address", "ip"],
        column_descriptions={"ip": "IP-адрес оборудования"},
    )

    payload = json.loads(output)

    assert payload["count"] == 1
    assert payload["items"] == [
        {
            "id": 755,
            "name": "Харцизская 155",
            "address": "Харцизская 155",
            "ip": "172.16.52.96",
        }
    ]
    assert payload["column_descriptions"] == {"ip": "IP-адрес оборудования"}


def test_build_records_output_marks_truncated_result():
    output = _build_records_output(
        rows=[
            {"id": 1, "name": "one"},
            {"id": 2, "name": "two"},
        ],
        limit=1,
        result_columns=["id", "name"],
        column_descriptions=None,
    )

    payload = json.loads(output)

    assert payload["count"] == 1
    assert payload["truncated"] is True
    assert payload["shown_limit"] == 1
    assert payload["items"] == [{"id": 1, "name": "one"}]
