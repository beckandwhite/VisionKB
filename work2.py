"""Work 2: OCR extraction for each picture."""

import work_common

NAME = "work2"
DEFAULT_PROMPT = "Extract all visible text from this picture. Return JSON: {\"text\": [\"line\"]}."


def run(source, config):
    try:
        response = work_common.vision_request(
            source["source_key"], config.get("work2_prompt", DEFAULT_PROMPT), config)
        parsed = work_common.parse_json_response(response)
        text = parsed.get("text", []) if isinstance(parsed, dict) else []
        if not isinstance(text, list):
            text = [str(text)]
        return work_common.result_record(source, {"text": [str(x) for x in text]})
    except Exception as exc:
        return work_common.result_record(source, error=exc)
