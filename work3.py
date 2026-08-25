"""Work 3: configurable vision classifier for each picture."""

import work_common

NAME = "work3"
DEFAULT_PROMPT = "Classify this picture. Return JSON: {\"class\": \"...\", \"confidence\": 0}."


def run(source, config):
    try:
        response = work_common.vision_request(
            source["source_key"], config.get("work3_prompt", DEFAULT_PROMPT), config)
        output = work_common.parse_json_response(response)
        return work_common.result_record(source, output)
    except Exception as exc:
        return work_common.result_record(source, error=exc)
