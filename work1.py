"""Work 1: generic vision query for each picture."""

from datetime import datetime, timezone

import work_common

NAME = "work1"
DEFAULT_PROMPT = "What is on this picture? Describe the important visible content."


def run(source, config):
    started = datetime.now(tz=timezone.utc).isoformat()
    prompt = config.get("work1_prompt", DEFAULT_PROMPT)
    try:
        answer = work_common.vision_request(source["source_key"], prompt, config)
        return work_common.result_record(
            source, NAME, source.get("modified_at"), started,
            datetime.now(tz=timezone.utc).isoformat(), {"answer": answer})
    except Exception as exc:
        return work_common.result_record(
            source, NAME, source.get("modified_at"), started,
            datetime.now(tz=timezone.utc).isoformat(), error=exc)
