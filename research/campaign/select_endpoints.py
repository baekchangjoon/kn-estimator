"""캠페인 엔드포인트 사전 등록 — 프로젝트별 endpoints-<name>.json 생성.

원본(reduce-token harness/endpoints.py의 N1/select_n8) 방법론을 일반화:
- N=1: w(정적 슬라이스)가 프로젝트 중앙값에 가장 가까운 EP (원본은 수동 사전 등록 —
  여기서는 재현 가능한 규칙으로 대체).
- N=K: seed=42, 컨트롤러 다양성 우선(중복 없이 못 채우면 그때부터 중복 허용),
  메서드 다양성 선호. 원본과 달리 컨트롤러 수 < K인 프로젝트를 허용해야 한다
  (tainted 서비스는 컨트롤러 1~3개).

인벤토리는 이 브랜치에서 수리된 kn_estimator 스캐너를 쓴다.
"""
import json
import random
import sys
from pathlib import Path

from kn_estimator import scan

HERE = Path(__file__).parent


def select_n1(inv, slices):
    ws = sorted(s["w_tokens"] for s in slices)
    med = ws[len(ws) // 2]
    best = min(zip(inv, slices), key=lambda t: (abs(t[1]["w_tokens"] - med),
                                                t[0]["controller"], t[0]["path"]))
    return [best[0]]


def select_nk(inv, k, exclude, seed=42):
    rng = random.Random(seed)
    key = lambda e: (e["method"], e["path"])
    pool = [e for e in inv if key(e) not in {key(x) for x in exclude}]
    if len(pool) < k:
        # 소형 프로젝트: N=k가 인벤토리 전체에 가까우면 N1 중복 제외를 포기한다.
        # 2점 분해는 독립 run 간 비교라 엔드포인트 중복이 수식을 해치지 않는다.
        pool = list(inv)
    assert len(pool) >= k, f"N={k} 선정 불가: 후보 {len(pool)}개"
    by_ctrl = {}
    for e in sorted(pool, key=lambda x: (x["controller"], x["path"], x["method"])):
        by_ctrl.setdefault(e["controller"], []).append(e)
    ctrls = sorted(by_ctrl)
    rng.shuffle(ctrls)
    picked, methods_seen = [], set()
    # 1라운드: 컨트롤러 중복 없이 + 새 메서드 선호, 2라운드: 새 메서드 조건 해제,
    # 3라운드: 컨트롤러 중복 허용 (원본에 없던 완화 — 컨트롤러 수 < K 프로젝트용)
    def one_pass(want_new_method, allow_dup_ctrl):
        progressed = False
        for c in ctrls:
            if len(picked) == k:
                return progressed
            if not allow_dup_ctrl and any(p["controller"] == c for p in picked):
                continue
            cands = [e for e in by_ctrl[c]
                     if e not in picked
                     and (not want_new_method or e["method"] not in methods_seen)]
            if not cands:
                continue
            e = rng.choice(cands)
            picked.append(e)
            methods_seen.add(e["method"])
            progressed = True
        return progressed

    one_pass(True, False)
    one_pass(False, False)
    while len(picked) < k and one_pass(False, True):
        pass  # 컨트롤러 수 < k: 중복 허용 패스를 채워질 때까지 반복
    assert len(picked) == k, f"N={k} 선정 부족: {len(picked)}"
    return sorted(picked, key=lambda x: (x["controller"], x["path"]))


def main():
    targets = json.loads((HERE / "targets.json").read_text())
    for name, t in targets.items():
        if name.startswith("_"):
            continue
        inv = scan.inventory(t["src"])
        slices = scan.build_slices(t["src"], inv)
        n1 = select_n1(inv, slices)
        data = {"inventory_count": len(inv), "n1": n1}
        for k in t["n_points"]:
            if k == 1:
                continue
            data[f"n{k}"] = select_nk(inv, k, exclude=n1)
        out = HERE / f"endpoints-{name}.json"
        out.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        print(f"{name}: inventory={len(inv)} n1={n1[0]['method']} {n1[0]['path']} "
              f"points={t['n_points']} -> {out.name}")


if __name__ == "__main__":
    sys.exit(main())
