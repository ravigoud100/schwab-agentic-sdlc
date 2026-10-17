"""Prepare the three assessment scenario review packets without source writes.

This command creates proposals and SQLite review-request records only.  It does
not call a model, manufacture human approval, run Maven, or release anything.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from governance import audit_logger
from governance.assessment_scenarios import prepare


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("directory", type=Path)
parser.add_argument("--workflow-id", default="assessment-scenarios")
args = parser.parse_args()
directory = args.directory.resolve()
audit_logger.DB_PATH = directory / "audit.db"
print(json.dumps(prepare(directory, args.workflow_id), indent=2))
