"""Template for adding a new per-source worker.

Copy this file to ``workN.py``, rename ``NAME``, and implement ``run``.
"""

import work_common

NAME = "workN"


def run(source, config):
    """Process one source and return a JSON-serializable result record."""
    output = {
        "message": "TODO: implement %s" % NAME,
    }
    return work_common.result_record(source, output)
