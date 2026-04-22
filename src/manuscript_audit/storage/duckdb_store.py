from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb
from pydantic import BaseModel

from manuscript_audit.utils.io import ensure_dir


class DuckDBRunStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        ensure_dir(self.db_path.parent)
        self.connection = duckdb.connect(str(self.db_path))
        self._initialize()

    def _initialize(self) -> None:
        statements = [
            (
                "CREATE TABLE IF NOT EXISTS runs "
                "(run_id TEXT, manuscript_id TEXT, input_path TEXT, output_dir TEXT)"
            ),
            (
                "CREATE TABLE IF NOT EXISTS parsed_artifacts "
                "(run_id TEXT, artifact_name TEXT, payload_json TEXT)"
            ),
            (
                "CREATE TABLE IF NOT EXISTS routing_decisions "
                "(run_id TEXT, decision_type TEXT, payload_json TEXT)"
            ),
            (
                "CREATE TABLE IF NOT EXISTS validator_findings "
                "(run_id TEXT, validator_name TEXT, payload_json TEXT)"
            ),
            (
                "CREATE TABLE IF NOT EXISTS report_artifacts "
                "(run_id TEXT, report_type TEXT, payload_json TEXT)"
            ),
        ]
        for statement in statements:
            self.connection.execute(statement)

    def _normalize_payload(self, payload: BaseModel | dict[str, Any] | list[Any]) -> Any:
        if isinstance(payload, BaseModel):
            return payload.model_dump(mode="json")
        if isinstance(payload, list):
            return [
                item.model_dump(mode="json") if isinstance(item, BaseModel) else item
                for item in payload
            ]
        return payload

    def _serialize(self, payload: BaseModel | dict[str, Any] | list[Any]) -> str:
        data = self._normalize_payload(payload)
        return json.dumps(data, sort_keys=True)

    def record_run(
        self,
        run_id: str,
        manuscript_id: str,
        input_path: str,
        output_dir: str,
    ) -> None:
        self.connection.execute(
            "INSERT INTO runs VALUES (?, ?, ?, ?)",
            [run_id, manuscript_id, input_path, output_dir],
        )

    def record_parsed_artifact(self, run_id: str, artifact_name: str, payload: Any) -> None:
        self.connection.execute(
            "INSERT INTO parsed_artifacts VALUES (?, ?, ?)",
            [run_id, artifact_name, self._serialize(payload)],
        )

    def record_routing_decision(self, run_id: str, decision_type: str, payload: Any) -> None:
        self.connection.execute(
            "INSERT INTO routing_decisions VALUES (?, ?, ?)",
            [run_id, decision_type, self._serialize(payload)],
        )

    def record_validator_result(self, run_id: str, validator_name: str, payload: Any) -> None:
        self.connection.execute(
            "INSERT INTO validator_findings VALUES (?, ?, ?)",
            [run_id, validator_name, self._serialize(payload)],
        )

    def record_report(self, run_id: str, report_type: str, payload: Any) -> None:
        self.connection.execute(
            "INSERT INTO report_artifacts VALUES (?, ?, ?)",
            [run_id, report_type, self._serialize(payload)],
        )

    def close(self) -> None:
        self.connection.close()
