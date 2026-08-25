"""Work 1: generic vision query for each picture."""

import work_common

NAME = "work1"
DEFAULT_PROMPT = "What is on this picture? Describe the important visible content."


def run(source, config):
    prompt = config.get("work1_prompt", DEFAULT_PROMPT)
    try:
        answer = work_common.vision_request(source["source_key"], prompt, config)
        return work_common.result_record(source, {"answer": answer})
    except Exception as exc:
        return work_common.result_record(source, error=exc)
