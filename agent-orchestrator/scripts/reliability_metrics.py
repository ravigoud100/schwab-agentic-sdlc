"""Read retained SQLite evidence without modifying it or resuming any workflow."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from governance.reliability_metrics import report,render

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest',type=Path,help='JSON list: audit_db, optional workflow_db/workflow_id/max_attempts, cohort, provenance')
    parser.add_argument('output',type=Path,help='New output directory')
    args=parser.parse_args()
    result=report(json.loads(args.manifest.read_text()))
    args.output.mkdir(parents=True,exist_ok=False)
    (args.output/'metrics.json').write_text(json.dumps(result,indent=2)+'\n')
    (args.output/'SUMMARY.md').write_text(render(result))
    print(args.output/'SUMMARY.md')
