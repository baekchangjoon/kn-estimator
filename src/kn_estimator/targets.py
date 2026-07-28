"""범용 앞단: --targets/--n 대상 목록 → 스캐너와 동일한 슬라이스 합성.

단위 의미(엔드포인트·클래스·파일·티켓…)는 사용자만 안다 — 여기서는
id·w·group만 다룬다. 파일럿의 n과 추정의 N은 같은 단위여야 한다
(단위 일관성 계약, docs/CONCEPTS.md).
"""
import json
import statistics
import sys
from pathlib import Path

OUTLIER_MEDIAN_MULT = 4   # 관례적 보수값 — 데이터 역검증 전까지 CLI 미노출
_NOGROUP = "\x00"          # 사용자 group과 충돌 불가한 무그룹 합성 키 접두


def is_explicit_group(name):
    return not name.startswith(_NOGROUP)


def _synth(items):
    """[{id,w,group}] → 스캐너 슬라이스 스키마. 무그룹은 항목별 고유 키 —
    단일 버킷으로 묶으면 FFD가 한 덩어리로 co-pack해 배치가 비효율해진다."""
    return [{"endpoint": {"method": "", "path": it["id"],
                          "controller": it["group"] or _NOGROUP + it["id"]},
             "w_tokens": it["w"], "unresolved": [], "external_call": False}
            for it in items]


def _file_tokens(p):
    """bytes/4, 최소 1 (0바이트 → w=0이면 w_mean 0 나눗셈). stat의 OSError는
    is_file() 통과 후의 TOCTOU 레이스 — 크래시 대신 균일값 폴백."""
    try:
        return max(1, int(p.stat().st_size / 4))
    except OSError:
        return 1


def _check_duplicates(ids, literal=False):
    """중복 id 검출. 텍스트 목록(literal=False)은 실존 파일을 resolve() 정규화해
    `./a`↔`a`를 잡고, JSON 목록(literal=True)은 명세대로 리터럴 비교만 한다.
    resolve()의 OSError/RuntimeError(심링크 루프·권한)는 리터럴 폴백.
    is_file()↔resolve() 사이의 TOCTOU 창은 로컬 CLI에서 실위험이 낮아 수용한다."""
    seen = {}
    for i in ids:
        key = i
        if not literal:
            p = Path(i)
            try:
                if p.is_file():
                    key = str(p.resolve())
            except (OSError, RuntimeError):
                pass
        if key in seen:
            raise SystemExit(f"--targets 목록에 중복 대상: '{seen[key]}'와 '{i}' — "
                             "같은 대상을 두 번 세면 비용이 과대추정된다.")
        seen[key] = i


def _parse_json(text, label):
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise SystemExit(f"--targets JSON 파싱 실패: {label}\n{e}")
    if not isinstance(data, list):
        raise SystemExit(f"--targets JSON은 배열이어야 한다: {label}")
    items = []
    for idx, it in enumerate(data):
        if not isinstance(it, dict) or not isinstance(it.get("id"), str) or not it["id"]:
            raise SystemExit(f"--targets 항목 {idx}: id(비어있지 않은 문자열)가 필요하다.")
        w = it.get("w", 1.0)
        if isinstance(w, bool) or not isinstance(w, (int, float)) or w <= 0:
            raise SystemExit(f"--targets 항목 {idx}('{it['id']}'): w는 양수여야 한다.")
        g = it.get("group")
        if g is not None and not isinstance(g, str):
            raise SystemExit(f"--targets 항목 {idx}('{it['id']}'): group은 문자열이어야 한다.")
        items.append({"id": it["id"], "w": float(w), "group": g})
    if not items:
        raise SystemExit(f"--targets 목록에 유효 항목이 없다: {label}")
    _check_duplicates([it["id"] for it in items], literal=True)
    return {"slices": _synth(items), "n": len(items), "w_source": "json",
            "source_label": label}


def parse_targets(arg, stdin=None):
    """arg: 목록 파일 경로 또는 '-'. 반환: {"slices", "n", "w_source", "source_label"}
    w_source ∈ {"file", "json", "uniform"} — 리포트 한계 고지 문구 분기용."""
    if arg == "-":
        try:
            text = (stdin or sys.stdin).read()
        except UnicodeDecodeError as e:
            raise SystemExit(f"--targets stdin이 UTF-8이 아니다: {e}")
        source_label = "stdin"
        if text.lstrip()[:1] in ("[", "{"):
            raise SystemExit("stdin은 텍스트 목록 전용이다 — JSON 목록은 "
                             ".json 파일로 지정하라 (--targets list.json).")
    else:
        p = Path(arg)
        if not p.is_file():
            raise SystemExit(f"--targets 목록 파일이 없다: {arg}")
        try:
            # 기존 관용(errors="replace")을 따르지 않는다 — id는 경로라서 뭉개면
            # "미실존 파일"이 되어 조용히 균일 w로 폴백한다. 시끄럽게 죽는다.
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError as e:
            raise SystemExit(f"--targets 목록이 UTF-8이 아니다: {arg}\n{e}")
        source_label = arg
        if arg.lower().endswith(".json"):
            return _parse_json(text, arg)
        if text.lstrip()[:1] in ("[", "{"):
            # 확장자만으로 판별하면 .txt에 담긴 JSON이 "한 줄 = 대상 하나"로
            # 조용히 오파싱돼 그럴듯한 오답(N=줄 수)이 나온다.
            raise SystemExit(f"--targets 목록이 JSON처럼 보인다 ({arg}) — "
                             "JSON 목록은 .json 확장자로 지정하라.")
    ids = [ln.strip() for ln in text.splitlines()]
    ids = [i for i in ids if i and not i.startswith("#")]
    if not ids:
        raise SystemExit(f"--targets 목록에 유효 항목이 없다: {source_label}")
    _check_duplicates(ids)
    paths = [Path(i) for i in ids]
    uniform_note = None
    if all(p.is_file() for p in paths):
        items = [{"id": i, "w": float(_file_tokens(p)), "group": str(p.parent)}
                 for i, p in zip(ids, paths)]
        w_source = "file"
    else:
        if any(p.is_file() for p in paths):
            # 부분 폴백 — 혼합 측정(실존=크기, 그 외=1)은 상대 비교를 왜곡한다.
            bad = [(i, p) for i, p in zip(ids, paths) if not p.is_file()]
            n_dir = sum(1 for _, p in bad if p.is_dir())
            n_miss = len(bad) - n_dir
            print(f"경고: {len(ids)}건 중 {len(bad)}건이 일반 파일이 아니라"
                  f"(미실존 {n_miss}건, 디렉터리 {n_dir}건) w를 균일 가정합니다. "
                  f"예: {', '.join(i for i, _ in bad[:5])}", file=sys.stderr)
        else:
            # 이름 문자열 목록 — 정상 사용, 경고 없음
            uniform_note = "목록에 파일 경로 없음"
        items = [{"id": i, "w": 1.0, "group": None} for i in ids]
        w_source = "uniform"
    return {"slices": _synth(items), "n": len(items), "w_source": w_source,
            "source_label": source_label, "uniform_note": uniform_note}


def n_targets(n):
    """--n: 합성 id unit-<k> (동적 제로패딩 — 사전식 정렬 = 숫자 순서), 균일 w."""
    pad = len(str(n))
    items = [{"id": f"unit-{k:0{pad}d}", "w": 1.0, "group": None}
             for k in range(1, n + 1)]
    return {"slices": _synth(items), "n": n, "w_source": "uniform",
            "source_label": f"--n {n}"}


def outliers(slices):
    """w > MULT×median 대상 (w 내림차순). N<4·median≤0이면 판정하지 않는다.
    균일 w에서는 정의상 결코 발동하지 않는다."""
    if len(slices) < 4:
        return []
    med = statistics.median(s["w_tokens"] for s in slices)
    if med <= 0:
        return []
    hits = [s for s in slices if s["w_tokens"] > OUTLIER_MEDIAN_MULT * med]
    return sorted(hits, key=lambda s: -s["w_tokens"])
