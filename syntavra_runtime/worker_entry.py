from __future__ import annotations
import argparse
from pathlib import Path
from .background_workers import BackgroundIntelligenceWorker

def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument('--project',required=True); parser.add_argument('--state-root',required=True); parser.add_argument('--interval',type=float,default=2.0)
    args=parser.parse_args(); BackgroundIntelligenceWorker(project=Path(args.project),state_root=Path(args.state_root)).run(iterations=None,interval_seconds=args.interval); return 0
if __name__=='__main__': raise SystemExit(main())
