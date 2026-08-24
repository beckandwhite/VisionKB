"""Work 3: configurable vision classifier for each picture."""

from datetime import datetime, timezone

import work_common

NAME = "work3"
DEFAULT_PROMPT = "Classify this picture. Return JSON: {\"class\": \"...\", \"confidence\": 0}."


def run(source, config):
    started = datetime.now(tz=timezone.utc).isoformat()
    try:
        response = work_common.vision_request(
            source["source_key"], config.get("work3_prompt", DEFAULT_PROMPT), config)
        output = work_common.parse_json_response(response)
        return work_common.result_record(
            source, NAME, source.get("modified_at"), started,
            datetime.now(tz=timezone.utc).isoformat(), output)
    except Exception as exc:
        return work_common.result_record(
            source, NAME, source.get("modified_at"), started,
            datetime.now(tz=timezone.utc).isoformat(), error=exc)
