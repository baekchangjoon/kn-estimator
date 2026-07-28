# targets 범용 앞단 구현 계획 (--targets / --n + 공통 이상치 감지)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Spring 스캐너 없이도 임의 반복 작업의 대상 목록(`--targets`/`--n`)으로 K·그룹·비용을 추정하게 하고, 모든 앞단에 w 이상치 감지·권고를 얹는다.

**Architecture:** 새 모듈 `targets.py`가 텍스트/stdin/JSON 목록을 검증·파싱해 스캐너와 동일한 슬라이스 스키마를 합성한다(뒷단 `plan.py`/`model.py` 무수정). `cli.py`는 소스 3종(스캐너/`--targets`/`--n`)을 상호 배타로 디스패치하고, 리포트·고지 어휘를 design spec §7 전수표대로 분기하며, 공통 이상치 감지를 삽입한다.

**Tech Stack:** Python 표준 라이브러리만 (argparse, json, pathlib, statistics). 테스트는 pytest — 기존 관용(monkeypatch `sys.argv` + `cli.main()` + capsys/tmp_path).

**요구사항명세:** docs/superpowers/requirements/2026-07-28-targets-frontend-requirements.md (REQ-001~013)
**design spec:** docs/superpowers/specs/2026-07-28-targets-frontend-design.md

## Global Constraints

- 뒷단(plan.py/model.py) 수정 금지 — 슬라이스 합성으로만 통합 (design D6).
- 스캐너 경로의 수치 결과(N=18 chunks=3 k_avg=6.0 est=$21.18, petclinic) 불변 (REQ-011). 리포트 골든은 이상치 라인 추가 시에만 재생성 + HANDOFF.md 갱신.
- 에러는 전부 한국어 SystemExit — raw traceback 노출 금지.
- 이상치 권고문에 '파일럿' 단어 금지 (design D9). `OUTLIER_MEDIAN_MULT=4`는 코드 상수, CLI 미노출 (D10).
- 무그룹 대상의 합성 키는 `"\x00" + id` — 어떤 사용자 산출물에도 노출 금지 (D8, REQ-007).
- 문서는 한국어 (README.en.md 제외). 커밋 메시지는 영어 관례.
- 각 태스크 완료 시 커밋. 커밋 트레일러:
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>` + `Claude-Session: https://claude.ai/code/session_01MrZjh1KjhRjk6uR77aN5Dv`

---

### Task 1: 수용 E2E 테스트 전체 작성 (외부 루프 RED)

**REQ-IDs:** REQ-001~013 (전 요구의 외부 루프)

**Files:**
- Create: `tests/test_targets_frontend.py` (REQ-011 테스트 포함 — test_kn.py 자체 러너는 픽스처 함수를 인자 없이 호출해 TypeError로 죽으므로 test_kn.py에는 넣지 않는다)

**Interfaces:**
- Consumes: 기존 `cli.main()`. SUT 경로 관행은 test_kn.py와 동일하게 재정의: `SUT = Path(os.environ.get("KN_SUT") or REPO / "petclinic")` (KN_SUT 오버라이드 존중).
- Produces: 매트릭스의 테스트 함수 전부 (test_req001_text_list … test_req013_concepts_doc_linked, test_req011_scanner_baseline_unchanged). 이후 태스크는 이 테스트들을 green으로 만든다.

- [ ] **Step 1: test_targets_frontend.py 작성** — 요구사항명세 추적 매트릭스의 함수명 그대로. 핵심 패턴:

```python
"""targets 범용 앞단 수용 E2E (REQ-001~013).

요구사항명세: docs/superpowers/requirements/2026-07-28-targets-frontend-requirements.md
"""
import io, json, sys
from pathlib import Path

import pytest

from kn_estimator import cli

REPO = Path(__file__).resolve().parent.parent


def _run(monkeypatch, argv, cwd=None, stdin=None):
    if cwd:
        monkeypatch.chdir(cwd)
    if stdin is not None:
        monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    monkeypatch.setattr(sys, "argv", ["kn-estimate"] + argv)
    cli.main()


def _files_list(tmp_path):
    d = tmp_path / "docs sub"          # 공백 포함 디렉터리 (REQ-004)
    d.mkdir()
    small = d / "small.md"; small.write_text("x" * 400)
    big = tmp_path / "big.md"; big.write_text("y" * 40_000)
    empty = d / "empty.md"; empty.write_text("")        # 0바이트 (REQ-004)
    lst = tmp_path / "list.txt"
    lst.write_text(f"{small}\n\n# 주석\n{big}\n{empty}\n")
    return lst


# REQ-001
def test_req001_text_list(tmp_path, monkeypatch, capsys):
    lst = _files_list(tmp_path)
    _run(monkeypatch, ["--targets", str(lst), "--label", "template",
                       "--model", "sonnet"], cwd=tmp_path)
    out = capsys.readouterr().out
    assert "N=3" in out
    assert (tmp_path / ".kn/kn-report.md").exists()     # out-dir는 cwd 기준
    assert (tmp_path / ".kn/kn-plan.json").exists()


# REQ-010 — 위치 검증 포함
def test_req010_outlier_warning_position(tmp_path, monkeypatch, capsys):
    items = [{"id": f"t{i}", "w": 100} for i in range(5)] + [{"id": "monster", "w": 900}]
    lst = tmp_path / "list.json"; lst.write_text(json.dumps(items))
    _run(monkeypatch, ["--targets", str(lst), "--groups"], cwd=tmp_path)
    out = capsys.readouterr().out
    assert "이상치" in out and "별도 라벨로 분리 측정" in out
    assert out.index("N=") < out.index("이상치") < out.index("그룹1(") < out.index("ℹ")
    warn_block = out[out.index("이상치"):out.index("그룹1(")]
    assert "파일럿" not in warn_block
    report = (tmp_path / ".kn/kn-report.md").read_text()
    assert "이상치" in report and "monster" in report
```

같은 파일에 매트릭스의 나머지 함수 전부를 요구사항명세의 Given-When-Then 그대로 작성한다 — REQ-002(stdin: `_run(..., stdin="a\nb\nc\n")` + `(stdin, 3건)` 리포트 확인; `test_req002_stdin_json_rejected`는 JSON 배열을 stdin으로 파이프 → "텍스트 목록 전용" SystemExit), REQ-003(`--n 100` → `N=100` + plan.json id `unit-001`/`unit-100`), REQ-004(w 상위 표 최상단=big.md, "그룹 단위" 동일 행 집계, cwd 밖 절대 경로 tmp 파일 fixture), REQ-005(실존2+미실존1+디렉터리1 → capsys.readouterr().err에 "미실존 1건"·"디렉터리 1건", 리포트 "균일 가정", 그룹 섹션 없음), REQ-006(6개 AC → `pytest.raises(SystemExit)` + 메시지 인덱스 확인, 여분 키 관용, `test_req006_json_duplicate_id`는 같은 id 2회 — 리터럴 비교), REQ-007(**용량 이내 2-group fixture** — 동봉 template/sonnet 계수로 `budget_soft = max(330000−66105.5−123895.25, δ_ep) ≈ 140,000`, 최소 그리드 cap(frac 0.4) ≈ 56,000이므로 group당 2~3항목·평균 w 규모면 어떤 frac에서도 분할되지 않는다 → plan.json에서 group별 청크 index 동일 + `\x00` 미노출 + `--groups` 헤더 소스 라벨), REQ-008(`[root, "--targets", …]` 동시·소스 0개), REQ-009(`./a.txt`↔`a.txt` 중복, 빈 목록, `--n 0`, 부재 파일), REQ-012(6개 AC — env-wall fixture는 **동봉 `data/calibration.json`을 로드해 베이스로 쓰고** `template/sonnet` 셀의 S0=200_000, delta_env=110_000, delta_ep=2_000만 오버라이드한 사본을 tmp_path에 저장해 `--calibration`으로 주입 — 셀에는 tau_env/tau_ep/out_env/out_ep/latency_s_per_turn, 최상위에는 pricing/version이 필요하므로 처음부터 만들면 KeyError가 난다), REQ-013(CONCEPTS.md 존재·README/README.en 링크·GUIDE `--targets`·CALIBRATION `--units` 부재).

REQ-011도 이 파일에 작성한다 (pytest 전용 — 픽스처 사용):

```python
import os
SUT = Path(os.environ.get("KN_SUT") or REPO / "petclinic")

def test_req011_scanner_baseline_unchanged(monkeypatch, capsys):
    """REQ-011: 스캐너 경로 수치 기준선 불변 (SUT 부재 시 skip — CI 허용)."""
    if not SUT.exists():
        pytest.skip(f"SUT 없음 ({SUT}) — KN_SUT 환경변수로 지정 가능")
    monkeypatch.setattr(sys, "argv", ["kn-estimate", str(SUT),
                                      "--label", "template", "--model", "sonnet"])
    cli.main()
    out = capsys.readouterr().out
    assert "N=18 chunks=3 k_avg=6.0 est=$21.18" in out
```

- [ ] **Step 2: RED 확인**

Run: `.venv/bin/python -m pytest tests/test_targets_frontend.py -q`
Expected: 전 테스트 FAIL (argparse가 `--targets` 미인식 → SystemExit 2) 또는 ERROR. REQ-013만 문서 부재로 FAIL, REQ-011은 SUT 부재 시 skip. **약화·주석 처리 금지.** 또한 `python tests/test_kn.py`를 직접 실행해 자체 러너가 깨지지 않음을 확인한다 (이 태스크는 test_kn.py를 수정하지 않는다).

- [ ] **Step 3: Commit** — `test: acceptance E2E for targets front-end (RED, REQ-001~013)`

---

### Task 2: targets.py — 파싱·검증·슬라이스 합성·이상치 헬퍼

**REQ-IDs:** REQ-001~006, REQ-009, REQ-010 (내부 루프)

**Files:**
- Create: `src/kn_estimator/targets.py`
- Test: `tests/test_targets_frontend.py` (Task 1의 E2E가 외부 루프; 파싱 단위 테스트를 같은 파일 하단에 추가)

**Interfaces:**
- Produces (Task 3이 사용):
  - `parse_targets(arg: str, stdin=None) -> dict` — `{"slices", "n", "w_source": "file"|"json"|"uniform", "source_label": str}`. 실패는 한국어 SystemExit.
  - `n_targets(n: int) -> dict` — 같은 반환형 (`w_source="uniform"`, `source_label=f"--n {n}"`).
  - `outliers(slices) -> list` — w > 4×median 슬라이스, w 내림차순 (N<4·median≤0이면 빈 리스트).
  - `is_explicit_group(name: str) -> bool` — 합성 무그룹 키 판별.
  - 상수 `OUTLIER_MEDIAN_MULT = 4`.

- [ ] **Step 1: 구현** (전문 — design §4·§5·§6 그대로):

```python
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
    단일 버킷로 묶으면 FFD가 한 덩어리로 co-pack해 배치가 비효율해진다."""
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
    resolve()의 OSError/RuntimeError(심링크 루프·권한)는 리터럴 폴백 — raw
    traceback 금지. is_file()↔resolve() 사이의 TOCTOU 창은 로컬 CLI에서
    실위험이 낮아 수용한다 (_file_tokens의 가드와 같은 부류)."""
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
    if arg == "-":
        text, source_label = (stdin or sys.stdin).read(), "stdin"
        if text.lstrip()[:1] in ("[", "{"):
            raise SystemExit("stdin은 텍스트 목록 전용이다 — JSON 목록은 "
                             ".json 파일로 지정하라 (--targets list.json).")
    else:
        p = Path(arg)
        if not p.is_file():
            raise SystemExit(f"--targets 목록 파일이 없다: {arg}")
        text, source_label = p.read_text(), arg
        if arg.endswith(".json"):
            return _parse_json(text, arg)
    ids = [ln.strip() for ln in text.splitlines()]
    ids = [i for i in ids if i and not i.startswith("#")]
    if not ids:
        raise SystemExit(f"--targets 목록에 유효 항목이 없다: {source_label}")
    _check_duplicates(ids)
    paths = [Path(i) for i in ids]
    if all(p.is_file() for p in paths):
        items = [{"id": i, "w": float(_file_tokens(p)), "group": str(p.parent)}
                 for i, p in zip(ids, paths)]
        w_source = "file"
    else:
        if any(p.is_file() for p in paths):
            bad = [(i, p) for i, p in zip(ids, paths) if not p.is_file()]
            n_dir = sum(1 for _, p in bad if p.is_dir())
            n_miss = len(bad) - n_dir
            print(f"경고: {len(ids)}건 중 {len(bad)}건이 일반 파일이 아니라"
                  f"(미실존 {n_miss}건, 디렉터리 {n_dir}건) w를 균일 가정합니다. "
                  f"예: {', '.join(i for i, _ in bad[:5])}", file=sys.stderr)
        items = [{"id": i, "w": 1.0, "group": None} for i in ids]
        w_source = "uniform"
    return {"slices": _synth(items), "n": len(items), "w_source": w_source,
            "source_label": source_label}


def n_targets(n):
    pad = len(str(n))
    items = [{"id": f"unit-{k:0{pad}d}", "w": 1.0, "group": None}
             for k in range(1, n + 1)]
    return {"slices": _synth(items), "n": n, "w_source": "uniform",
            "source_label": f"--n {n}"}


def outliers(slices):
    """w > MULT×median 대상 (w 내림차순). N<4·median≤0이면 판정하지 않는다.
    균일 w에서는 정의상 결코 발동하지 않는다 — 경고가 아니라 사실이다."""
    if len(slices) < 4:
        return []
    med = statistics.median(s["w_tokens"] for s in slices)
    if med <= 0:
        return []
    hits = [s for s in slices if s["w_tokens"] > OUTLIER_MEDIAN_MULT * med]
    return sorted(hits, key=lambda s: -s["w_tokens"])
```

- [ ] **Step 2: 파싱 단위 테스트 실행** — Task 1 E2E 중 SystemExit 계열(REQ-006, REQ-009)은 cli 연결 전엔 여전히 RED다. `parse_targets`/`n_targets`/`outliers`를 직접 호출하는 단위 테스트(`test_unit_parse_text_file_w`, `test_unit_parse_json_ok`, `test_unit_outliers_below_n4`, `test_unit_outliers_median_uniform_silent`, `test_unit_n_targets_padding`)를 test_targets_frontend.py 하단에 추가하고 green 확인:

Run: `.venv/bin/python -m pytest tests/test_targets_frontend.py -k "unit_" -q`
Expected: PASS

- [ ] **Step 3: Commit** — `feat(targets): generic front-end parsing, slice synthesis, outlier helper`

---

### Task 3: cli.py — 소스 디스패치·범용 파이프라인·리포트 어휘

**REQ-IDs:** REQ-001, 002, 003, 008, 012

**Files:**
- Modify: `src/kn_estimator/cli.py`

**Interfaces:**
- Consumes: Task 2의 `parse_targets`/`n_targets`/`is_explicit_group`.
- Produces: 소스 무관 공통 리포트 빌더. `_display(s)` 헬퍼 — `--groups`·plan.json·상위-10 표가 공유 (`method`가 비면 `path`만).

- [ ] **Step 1: 인자·디스패치** — `project_root`를 `nargs="?"`로, `--targets`, `--n type=int` 추가. 파싱 직후:

```python
sources = [x for x in (args.project_root, args.targets, args.n) if x is not None]
if len(sources) != 1:
    raise SystemExit("입력 소스는 정확히 하나여야 한다 — <project_root> | "
                     "--targets <파일|-> | --n <개수> 중 하나를 지정하라.")
if args.n is not None and args.n <= 0:
    raise SystemExit(f"--n 은 양수여야 한다 (받은 값: {args.n})")
```

스캐너 분기(기존 root 검사·inventory·build_slices)는 `args.project_root`가 있을 때만 실행. 범용 분기는 `meta = targets.parse_targets(args.targets)` 또는 `targets.n_targets(args.n)` → `sls = meta["slices"]`. 공통 변수: `generic = args.project_root is None`, `noun = "대상" if generic else "엔드포인트"`, `noun_short = "대상" if generic else "EP"`.

- [ ] **Step 2: project_root 참조 전부 가드** — out 디렉터리(`out = (Path.cwd() if generic else Path(args.project_root)) / args.out_dir`), `--groups` 헤더(`hdr = meta["source_label"] if generic else Path(args.project_root).resolve().name`; stdin은 `"stdin"`, 목록 파일은 그 경로 문자열), `대상:` 줄(`args.project_root` 또는 `(stdin, N건)`/목록 경로/`(--n N)`).

- [ ] **Step 3: 어휘 분기 — design §7 표의 전 행.** `_env_wall_warning`의 시그니처를 `(cal, label, mdl, w_soft, noun_short="EP")`로 바꾸고 본문의 하드코딩 `"EP당 1청크로 퇴화"`를 `f"{noun_short}당 1청크로 퇴화"`로 교체(호출부도 인자 전달). 파일럿 고지(cli.py:333)의 `"EP 1개짜리"` → `f"{noun_short} 1개짜리"`. plan.json의 chunks endpoints 문자열도 `_display(s)`로 생성한다 (method가 비면 선행 공백이 남는 f-string 직접 조립 금지 — 스캐너 출력은 동일 바이트 유지). 리포트: N 줄 명사·미해결 절 생략, w 분포 줄은 균일이면 `- w: 균일 가정` + (파일 경로 없음이면 `(목록에 파일 경로 없음)` 병기), 컨트롤러 섹션 → 그룹 섹션(§7 규칙 — Task 4), 상위-10 섹션(`## w 상위 10 대상`, `| 대상 | w (tokens) |`, 균일이면 생략), 한계 고지(정적 슬라이스 bullet 생략, w 출처별 문구: file=`w는 파일 크기(bytes/4)다`, json=`w는 사용자 제공값이다`, uniform=`w는 균일 가정이다`). 스캐너 분기 출력은 **바이트 단위로 기존과 동일**해야 한다.

- [ ] **Step 4: green 확인**

Run: `.venv/bin/python -m pytest tests/test_targets_frontend.py -k "req001 or req002 or req003 or req008 or req012" -q`
Expected: PASS. 그리고 전 기존 스위트: `.venv/bin/python -m pytest -q` — 기존 테스트 무회귀.

- [ ] **Step 5: Commit** — `feat(cli): source dispatch and generic-source report vocabulary`

---

### Task 4: group 섹션·--groups·w 자동/폴백 E2E green

**REQ-IDs:** REQ-004, 005, 006, 007, 009

**Files:**
- Modify: `src/kn_estimator/cli.py` (그룹 섹션 필터·집계)

**Interfaces:**
- Consumes: `targets.is_explicit_group`.

- [ ] **Step 1: 그룹 섹션** — controllers 집계는 기존 루프 재사용하되, 범용 소스에서는 `is_explicit_group(name)`인 키만 표에 싣고 헤더를 `## 그룹 단위` / `| 그룹 | n | Σw (tokens) | 배정 청크 |`로, 명시 그룹이 0이면 섹션 전체(헤더 포함)를 생략한다. research 각주 인용문(컨트롤러 분산 검정)은 스캐너 전용 — 범용 소스에서 생략.

- [ ] **Step 2: green 확인**

Run: `.venv/bin/python -m pytest tests/test_targets_frontend.py -k "req004 or req005 or req006 or req007 or req009" -q`
Expected: PASS

- [ ] **Step 3: Commit** — `feat(cli): group section, auto-w, fallback paths for generic sources`

---

### Task 5: 공통 이상치 감지 + 스캐너 통합·골든 재생성

**REQ-IDs:** REQ-010, REQ-011

**Files:**
- Modify: `src/kn_estimator/cli.py`
- Modify: `HANDOFF.md` (골든 해시·기준선 — 변경 시)

**Interfaces:**
- Consumes: `targets.outliers`, `_display(s)`.

- [ ] **Step 1: 삽입** — cli.py에 `import statistics` 추가. 리포트: N/w 줄 블록 직후(`## 권장 플랜` 앞)에 이상치 블록:

```python
outs = targets.outliers(sls)
if outs:
    med = statistics.median(s["w_tokens"] for s in sls)
    listing = ", ".join(f"{_display(s)} (w={s['w_tokens']:,.0f}, {s['w_tokens']/med:.0f}배)"
                        for s in outs[:5])
    outlier_msg = (f"⚠ 이상치 {len(outs)}건: {listing} — 이 {noun}들은 나머지와 크기가 "
                   "이질적입니다. 현재 셀 계수로의 외삽은 과소추정 위험이 있어 "
                   "별도 라벨로 분리 측정을 권장합니다.")
```

stdout: `print(f"N={n} ...")` 요약 줄 **직후**에 같은 `outlier_msg` 출력 (`--groups` 블록·파일럿 고지 ℹ보다 앞 — 기존 순서 계약 `test_pilot_notice_follows_groups_block` 불변).

- [ ] **Step 2: 스캐너 경로 영향 실측** — `KN_SUT` 로컬 클론으로 기준 명령 실행: petclinic w 분포에서 이상치가 발동하는지 확인. 발동하면 kn-report.md 골든 해시 재산출(문서화된 상대 경로 호출로), HANDOFF.md의 해시·주의 문구 갱신. 수치 요약(`N=18 chunks=3 k_avg=6.0 est=$21.18`)은 어떤 경우에도 불변이어야 한다 — 달라지면 STOP, 원인 규명. **SUT를 구할 수 없는 환경이면**: 골든을 추측으로 갱신하지 말고, HANDOFF.md에 "이상치 라인 반영 여부 미실측 — 골든 재검증 필요"를 명시적 후속 과제로 기록하고 PR 본문에도 표기한다 (조용한 skip 금지).

- [ ] **Step 3: green 확인**

Run: `.venv/bin/python -m pytest tests/test_targets_frontend.py -k req010 -q && .venv/bin/python -m pytest tests/test_kn.py -q`
Expected: PASS (SUT 부재 환경이면 REQ-011은 skip)

- [ ] **Step 4: Commit** — `feat(cli): common w-outlier detection with split-measurement advisory`

---

### Task 6: 문서 — CONCEPTS.md 신설 + 4개 문서 동기화

**REQ-IDs:** REQ-013

**Files:**
- Create: `docs/CONCEPTS.md`
- Modify: `README.md`, `README.en.md`, `docs/GUIDE.md`, `docs/CALIBRATION.md`, `HANDOFF.md`

**Interfaces:** 없음 (문서).

- [ ] **Step 1: docs/CONCEPTS.md 작성** — 초급 엔지니어 대상, 전 개념에 실행 예시. 필수 목차 (design §8):
  1. 큰 그림 — 앞단/뒷단 그림(ASCII), "툴은 단위를 모른다"
  2. 앞단 3종 — 스캐너(엔드포인트 열거·DI 추적 w를 petclinic 예시로), `--targets`(텍스트/JSON), `--n`
  3. w란 무엇인가 — 상대 크기, 스케일 불변, 균일 가정이 허용되는 근거(§4.7 R²≈0)와 한계
  4. 캘리브레이션·라벨·셀 — 파일럿 2점, `<label>/<model>`
  5. 이상치 감지 — 4×median, 균일 w 경로에선 불발, 권고의 의미
  6. 단위 일관성 계약 — 파일럿 n과 추정 N은 같은 단위 (비경로 id 중복 미탐지 한계 포함)
  7. 단위별 요리책 표 — 파일/클래스/메소드/엔드포인트/기타 레시피 (2026-07-28 대화 표)
- [ ] **Step 2: 문서 동기화** — README 퀵스타트에 `--targets` 파일 단위 예제 1개 + 옵션 표 `--targets`/`--n` 행 + 문서 표 CONCEPTS.md 행 (README.en 동문). GUIDE.md 옵션 레퍼런스에 §3~§7 요약. CALIBRATION.md §6: 제목 "(로드맵, 미구현)" 제거, `--units` 어휘 → `--targets` 구현 완료로 개서, 단위 일관성 계약 명시. HANDOFF.md: 새 기능 한 줄 + 골든 갱신분(Task 5).
- [ ] **Step 3: green 확인**

Run: `.venv/bin/python -m pytest tests/test_targets_frontend.py -k req013 -q`
Expected: PASS

- [ ] **Step 4: Commit** — `docs: CONCEPTS beginner guide + targets front-end doc sync`

---

### Task 7: 전체 회귀·매트릭스 100%·마무리

**REQ-IDs:** 전체 (DoD 확인)

**Files:**
- Modify: `docs/superpowers/requirements/2026-07-28-targets-frontend-requirements.md` (Status 🔴→🟢)

- [ ] **Step 1: 전체 스위트**

Run: `.venv/bin/python -m pytest -q` + `KN_SUT` 보유 시 `tests/test_kn.py` 자체 러너
Expected: 전부 PASS (SUT 의존은 로컬 green 확인)

- [ ] **Step 2: 매트릭스 갱신** — 각 REQ 행을 실제 통과 테스트명과 대조해 🟢로. Coverage 줄 13/13.
- [ ] **Step 3: Commit** — `docs(requirements): matrix all green (13/13)`

이후는 plan 밖 PR 게이트(dev-workflow): spec-compliance 리뷰 → code-quality 리뷰 → PR → CI → rebase merge.

## Self-Review 기록

1. **Spec coverage:** design §3(Task 3)·§4(Task 2)·§5(Task 2)·§6(Task 5)·§7(Task 3·4)·§8(Task 6)·§9 E1~E13(Task 1) — 전 섹션 태스크 대응 확인. REQ-001~013 전부 태스크 헤더에 매핑.
2. **Placeholder:** 코드 블록 없는 구현 스텝 없음 확인 (Task 3 Step 3은 대상 리터럴을 §7 표로 지정 — 표 자체가 전수 명세).
3. **Type consistency:** `parse_targets`/`n_targets` 반환형 4키 통일, `is_explicit_group`/`outliers`/`_display` 명칭 태스크 간 일치 확인.
