from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.request

EXPECTED = {
    0: (8000, "2466b0a6ae0f65b266c981b788978d759627f1038e6336d7e5e10eff0920c5c7"),
    1: (8000, "99fb16797035f4b26c246447fc21d600faf201f62d3a5f3e9c2be13bf35b6376"),
    2: (8000, "d75cd8dd3415e73fc37055f30414a19aa8a134a8474b0b655cdb61eee32c8d90"),
    3: (8000, "726cf2cc57eb8e90f14f5ce7f38d15320ec62a4199c2d66945b7e382891392c9"),
    4: (8000, "1e0d08102f638e2c52302382b0e44366f23a605d8c3fc73e281efc7b910ecbd0"),
    5: (8000, "fa195be77bf482f3673e6b91fc1e6533e9a14e57253aa78c1922a46273a77c3e"),
    6: (8000, "7a7b8c4e26c18e23631d606c2c7400d751e731684e8e8809824328a78d0ee1fc"),
    7: (8000, "e703ff289f62215b035f21379dc1749d1faba4a58149db6c0a935c9639bed9bd"),
    8: (8000, "601f79a0a4e44831ee3794f21514801779878845576fe1398a2da295780232d7"),
    9: (8000, "4f2f677d9ca7b75c1094b831ee57e6ab4eb3a3a268a5c3bce7ee3e9b4da54de3"),
    10: (8000, "8fdb6f15161e33b169ea160145b7b28d183ee45fb5805fc90324af9b7db750ad"),
    11: (8000, "edf98a9ba867ac0875281516589d60b3e61a68f228be05f7558305a454107ce0"),
    12: (8000, "913e5a684246920d3ca6b4ec57eca6c3f20db2f19662f27ea483a1a8b2284c35"),
    13: (8000, "9a247c4a95efe0eb9ba8bda5d146d272e9a3f808d5e2f56954f75a397d4932b0"),
    14: (8000, "e5ab0a6fd44d1cc9b4e16e598f427db865cfffdf4fddd512beeced257f9a5c77"),
    15: (8000, "3cf7f411890f311c6e72508f87208cc16a43ccbba64cbef3134df80784da222d"),
    16: (764, "f49fa911c1b063c237103d93045232e4b97e2b09ce89f9dc4e2f5b32868e2905"),
}


def request_json(url: str, headers: dict[str, str], *, payload: dict[str, object] | None = None) -> object:
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(url, headers=headers, data=data)
    if payload is not None:
        request.method = "POST"
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def main() -> None:
    repository = os.environ["REPOSITORY"]
    headers = {
        "Authorization": f"Bearer {os.environ['GH_TOKEN']}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "syntavra-consolidation-bootstrap",
    }
    comments: list[dict[str, object]] = []
    page = 1
    while True:
        rows = request_json(
            f"https://api.github.com/repos/{repository}/issues/62/comments?per_page=100&page={page}",
            headers,
        )
        assert isinstance(rows, list)
        comments.extend(rows)
        if len(rows) < 100:
            break
        page += 1

    pattern = re.compile(
        r"<!-- SYNTAVRA_V4_PART index=(\d+) sha256=([0-9a-f]{64}) -->\n(.*?)\n<!-- SYNTAVRA_V4_END -->",
        re.S,
    )
    found: dict[int, str] = {}
    observed: dict[int, list[str]] = {}
    for row in comments:
        for match in pattern.finditer(str(row.get("body") or "")):
            index = int(match.group(1))
            if index not in EXPECTED:
                continue
            length, wanted = EXPECTED[index]
            candidate = match.group(3)[:length]
            actual = hashlib.sha256(candidate.encode()).hexdigest()
            observed.setdefault(index, []).append(actual)
            if actual == wanted:
                found[index] = candidate

    missing = sorted(set(EXPECTED) - set(found))
    result = {
        "found": sorted(found),
        "missing": missing,
        "observed_for_missing": {str(i): observed.get(i, []) for i in missing},
        "comment_count": len(comments),
    }
    body = "<!-- SYNTAVRA_BOOTSTRAP_AUDIT -->\n```json\n" + json.dumps(result, indent=2) + "\n```"
    request_json(f"https://api.github.com/repos/{repository}/issues/63/comments", headers, payload={"body": body})
    print(json.dumps(result))
    if missing:
        raise SystemExit(1)
    transport = "".join(found[index] for index in sorted(found))
    assert len(transport) == 128764
    assert hashlib.sha256(transport.encode()).hexdigest() == "0fe5731bceef753ca25d2861a30ae29d27a1574eec514be7208be650e29c9219"


if __name__ == "__main__":
    main()
