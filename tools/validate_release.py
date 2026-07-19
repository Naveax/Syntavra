#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from signalcore_runtime.benchmark_harness import TIER_CONFIGS, validate_config
from signalcore_runtime.claim_governance import decide_claim
from signalcore_runtime.difficulty import evaluate_difficulty
from signalcore_runtime.util import atomic_write_json

CONTROLS={name:True for name in ("same_prompt","same_model","same_reasoning","same_repository","same_verifier","same_permissions","same_timeout","balanced_cache","no_artificial_sleep","no_meaningless_duplication")}

def main(argv=None):
    parser=argparse.ArgumentParser(); parser.add_argument("--profile",default="5x"); parser.add_argument("--smoke",action="store_true"); parser.add_argument("--output"); args=parser.parse_args(argv)
    tiers={tier:validate_config({"tier":tier,"axes":axes,"controls":CONTROLS}) for tier,axes in TIER_CONFIGS.items()}; difficulty=evaluate_difficulty("20X",TIER_CONFIGS["20X"],integrity=CONTROLS); claim=decide_claim(tier="20X",baseline_costs=[],signalcore_costs=[],difficulty=difficulty,actual_quota_available=False)
    result={"ok":all(value["ok"] for key,value in tiers.items() if key!="1X") and claim.claim=="5X_NOT_PROVEN","profile":args.profile,"difficulty":tiers,"claim_ceiling":asdict(claim),"note":"Difficulty construction is verified. Live paired provider/quota performance is not proven."}
    if args.output: atomic_write_json(Path(args.output),result,mode=0o644)
    print(json.dumps(result,indent=2,sort_keys=True)); return 0 if result["ok"] else 2

if __name__ == "__main__": raise SystemExit(main())
