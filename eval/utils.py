import os
import json
import random
import logging
from datetime import datetime

EVAL_RESULTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "eval_results")


def ensure_output_dir(subdir):
    path = os.path.join(EVAL_RESULTS_DIR, subdir)
    os.makedirs(path, exist_ok=True)
    return path


def save_results(data, subdir, prefix):
    out_dir = ensure_output_dir(subdir)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = os.path.join(out_dir, f"{prefix}_{ts}.json")
    with open(filepath, "w") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"Results saved to {filepath}")
    return filepath


def load_results(filepath):
    with open(filepath, "r") as f:
        return json.load(f)


def load_config(config_path=None):
    if config_path is None:
        config_path = os.path.join(os.path.dirname(__file__), "configs", "default.json")
    with open(config_path, "r") as f:
        return json.load(f)


def subsample_cases(cases, n, seed=42):
    if n >= len(cases):
        return cases
    rng = random.Random(seed)
    return rng.sample(cases, n)


def truncate_traces(cases, max_len):
    result = []
    for c in cases:
        truncated_events = c["events"][:max_len]
        result.append({"id": c["id"], "events": truncated_events})
    return result


def setup_logging(name):
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(message)s"))
        logger.addHandler(handler)
    return logger
