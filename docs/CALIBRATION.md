# 캘리브레이션 실전 가이드 — 원장·트랜스크립트는 누가 어떻게 만드나

> `kn-calibrate --ledger run_ledger.jsonl --runs runs/ --out my-cal.json`을 처음 볼 때
> 드는 질문들의 답: 이 파일들은 어디서 오고, 자동으로 생기는지, 에이전트에게는
> 뭐라고 시키는지, Claude Code가 아닌 에이전트에서는 어떻게 하는지.
> 모델 원리는 [cost-model-explained.md](cost-model-explained.md), CLI 요약은
> [GUIDE.md](GUIDE.md) §4.4 참조.

## 1. 전체 그림

```
① 파일럿 실측 — 실제 LLM 에이전트로 테스트 생성 그룹을 돌린다 (여기만 돈이 든다)
     └→ run(세션)마다 두 가지를 남긴다:
         run_ledger.jsonl               ← run당 1줄 "합계" (사람이/스크립트로 기록)
         runs/<run_id>/transcript.jsonl  ← 세션 로그 (에이전트가 자동 기록한 파일을 복사)
② kn-calibrate --ledger run_ledger.jsonl --runs runs/ --out my-cal.json
     └→ 셀(라벨×모델)별 계수로 분해 (env/ep 2점 fit)
③ 게이트 통과 세션의 컨텍스트 분포로 --w-soft 재산정
④ kn-estimate <root> --calibration my-cal.json --w-soft <재산정값>
```

`kn-estimate`는 정적 스캔이라 혼자 돌지만, `kn-calibrate`의 입력은 실측 기록이라
**도구 밖에서** 만들어 줘야 한다. 실측(±10% 정확도) 없이 상대 비교만 필요하면
이 문서 전체를 건너뛰고 동봉 캘리브레이션으로 쓰면 된다.

## 2. 두 입력 파일 — 자동인가, 수동인가

| 파일 | 생성 주체 | 자동? |
|---|---|---|
| `runs/<run_id>/transcript.jsonl` | 에이전트가 세션 로그로 **자동 기록** — Claude Code는 `~/.claude/projects/<프로젝트-경로-슬러그>/<세션id>.jsonl`. 그 파일을 복사만 하면 된다 | 반자동 (복사 1회) |
| `run_ledger.jsonl` | **수동** — run당 1줄을 직접 기록. 아래 §4의 스니펫이 트랜스크립트에서 합계를 계산해 준다 | 수동 (스니펫 보조) |

원장이 "합계"(총비용·총출력·게이트)를, 트랜스크립트가 "턴 단위 관측"(S0 첫 턴
컨텍스트, 턴 수 τ, 최대 컨텍스트 cmax)을 담당한다. 트랜스크립트 없는 run은
`missing_transcript`로 계수에서 제외된다.

## 3. 파일럿 실측 절차

1. **플랜 산출**: `kn-estimate <root> --groups` — 그룹 구성을 얻는다.
2. **run 2개 선택 — 크기가 달라야 한다**: 같은 라벨×모델로 **EP 1개짜리** run과
   **최소 그룹** run. kn-calibrate의 env/ep 분해가 N 2점의 1차 fit이라, 같은 크기
   2개나 1개짜리 하나로는 계수가 나오지 않는다 (반복 2~3회면 분산 밴드까지
   실측 기반이 된다 — ±10% 수치는 N 2점×반복 3 설계의 실측이다).
3. **에이전트에게 시키기** — 새 독립 세션에서 아래 템플릿으로 지시한다
   (그룹 경계를 명시하지 않으면 에이전트가 다른 EP까지 만들어 N이 오염된다):

   ```text
   <root>의 다음 엔드포인트에 대한 out-of-process 블랙박스 API 테스트를 생성하라:
     GET /api/owners/{ownerId}          ← kn-plan.json 그룹의 EP만 나열
   이 목록에 없는 엔드포인트는 절대 다루지 마라. 완료 기준은 생성한 테스트가
   컴파일되는 것까지다. 완료하면 다른 작업 없이 종료하라.
   ```

4. **세션 종료 후 기록** (run_id 명명 규약: `<프로젝트>_<label>-<model>-n<EP수>-r<반복>`)

   ```bash
   mkdir -p runs/myproj_template-sonnet-n1-r1
   cp ~/.claude/projects/<슬러그>/<세션id>.jsonl \
      runs/myproj_template-sonnet-n1-r1/transcript.jsonl
   ```

   원장 한 줄은 §4 스니펫으로 계산해 `run_ledger.jsonl`에 append.
5. **게이트 판정**: `gate: "pass"`는 **컴파일 통과 기준**을 권장한다 — 다중
   프로젝트 캠페인이 쓴 기준이고(스타일 밸리데이터는 오탐이 많아 제외했다),
   실패 run은 조기 종료라 비용이 과소해 계수를 오염시키므로 반드시 정직하게
   기록한다. 테스트 실행 통과까지 요구할지는 프로젝트 선택이되, 기준을 중간에
   바꾸면 셀 간 비교가 무너진다.
6. **재캘리브레이션과 벽 재산정**: `kn-calibrate ... --out my-cal.json` 후,
   게이트 통과 세션의 컨텍스트 분포(p50~max)로 `--w-soft`를 재산정해 재실행한다.

## 4. 원장 스키마와 기록 스니펫

run당 1줄 JSON (필수 필드):

```json
{"run_id": "myproj_template-sonnet-n1-r1", "label": "template", "model": "sonnet",
 "role": "run_total", "n": 1, "rep": 1, "gate": "pass",
 "cost_usd": 5.16, "output_tokens": 69007, "wall_s": 818, "harness": "claude-code"}
```

셀은 `label/model`로 정의된다 — `label`은 작업 유형의 자유 이름표다 (동봉 실측의
라벨: `template`·`flat` 두 생성 전략). 임의 작업(예: 클래스 분석)이라면 자기 라벨
하나로 충분하다.

트랜스크립트에서 합계를 계산하는 스니펫 (Claude Code 트랜스크립트 기준; 캠페인
실측 원장과 오차 ~1% 이내 — 서브에이전트 사용량은 별도 트랜스크립트라 미포함):

```python
import json, sys
# 사용법: python ledger_line.py <transcript.jsonl> <model: sonnet|opus|haiku>
PRICE = {"sonnet": (3.0, 15.0), "opus": (5.0, 25.0), "haiku": (1.0, 5.0)}
p_in, p_out = PRICE[sys.argv[2]]
seen = {}
for line in open(sys.argv[1]):
    try: m = json.loads(line)
    except Exception: continue
    msg = m.get("message") or {}
    if m.get("type") == "assistant" and msg.get("id") and msg.get("usage"):
        seen[msg["id"]] = msg["usage"]
cost = out = 0
for u in seen.values():
    cost += (u.get("input_tokens", 0) * p_in
             + u.get("cache_read_input_tokens", 0) * p_in * 0.1    # 캐시 읽기 0.1×
             + u.get("cache_creation_input_tokens", 0) * p_in * 2  # 1h 캐시 쓰기 2×
             + u.get("output_tokens", 0) * p_out) / 1e6
    out += u.get("output_tokens", 0)
print(f'"cost_usd": {cost:.2f}, "output_tokens": {out}, "turns": {len(seen)}, '
      f'"harness": "claude-code"')
```

## 5. Claude Code가 아닌 에이전트에서 쓰기 (Kiro, Antigravity, Cursor 등)

두 입력의 요구가 다르다:

- **원장은 도구 무관이다** — 어떤 에이전트든 그 run의 총비용·총출력토큰·게이트만
  옮겨 적으면 된다 (비용은 해당 에이전트의 과금 체계로 계산). 원장 라인의
  `harness` 필드에 하네스 이름을 기입하라 — kn-calibrate는 **같은 셀에 서로 다른
  하네스가 섞이면 경고**한다 (필드가 없는 기존 원장 행은 `claude-code`로 간주).
- **트랜스크립트는 형식 계약이 있다.** `kn-calibrate`가 읽는 최소 스키마는
  "한 줄 = JSON 하나, 턴(assistant 응답)마다":

  ```json
  {"type": "assistant",
   "message": {"id": "<턴별 고유값>",
               "usage": {"cache_read_input_tokens": <그 턴에 모델이 읽은 컨텍스트>,
                         "input_tokens": 0, "cache_creation_input_tokens": 0}}}
  ```

  캐시 구분이 없는 에이전트는 그 턴의 총 입력 컨텍스트를
  `cache_read_input_tokens`에 넣고 나머지를 0으로 두면 된다 — kn-calibrate는 세
  필드의 합으로 S0(첫 턴)·cmax(최대)·τ(줄 수)만 계산한다. 자기 에이전트의 세션
  로그를 이 형태로 바꾸는 변환 스크립트 하나가 곧 어댑터다.

### 실증된 어댑터 예시 — Kiro CLI (`kn-kiro`)

Kiro CLI용 어댑터가 패키지에 동봉돼 있다 — 설치 시 `kn-kiro` 명령
(계획·스키마 근거: `docs/superpowers/plans/2026-07-28-kiro2kn-adapter-plan.md`).
Kiro의 sqlite(`~/Library/Application Support/kiro-cli/data.sqlite3`,
conversations_v2)에서 턴별 컨텍스트를 복원해 계약 형식으로 변환한다:

```bash
# 가장 흔한 경로 — 파일럿 세션 종료 직후, 그 프로젝트 디렉토리에서 한 줄.
# --latest가 현재 디렉토리의 가장 최근 Kiro 대화를 자동 선택하고, --label/--model은
# kn-estimate와 같은 어휘다 (runs/·run_ledger.jsonl은 기본값이라 생략 가능).
kn-kiro --latest --label template --model sonnet --n 1 --gate pass --cost 0.42

# 세션을 명시 선택해야 할 때:
kn-kiro --list --cwd ~/work/my-backend
kn-kiro c7719363 --label template --model sonnet --n 1 --gate pass --cost 0.42
```

한계: 현 Kiro 버전은 턴별 토큰 필드를 채우지 않아 컨텍스트는
`context_usage_percentage×윈도우`로, 출력 토큰은 응답 본문 바이트/4로
근사한다(원장·셀에 `out_approx: true`로 표기됨). 비용은 Kiro 크레딧의 USD
환산액을 `--cost`로 직접 지정한다.

- **주의 — 계수는 하네스의 함수다.** S0는 지침·시스템 프롬프트 크기, τ·out_env는
  에이전트의 행동 패턴에서 온다 (실측: Kiro S0≈13.8K vs Claude Code 66K).
  에이전트를 바꾸면 **반드시 그 에이전트로 재캘리브레이션**해야 하고, 셀의 실질
  의미는 "라벨×모델×하네스"다. 동봉 캘리브레이션은 Claude Code 하네스 실측이므로
  타 에이전트에서는 상대 비교 참고치로만 쓰라.

## 6. 엔드포인트 너머 — 범용화 (로드맵, 미구현)

비용 모델 자체는 API 테스트에 특화된 것이 없다. 2차→1차 구조와 env/ep 분해가
요구하는 것은 "**반복 단위** N개와 단위별 크기 w뿐"이고, Spring에 묶인 부분은
인벤토리 스캐너(①단계)뿐이다. 따라서:

- **적용 가능**: 문서 페이지 번역, 클래스 단위 리팩토링, 파일 단위 마이그레이션,
  모듈별 문서화 — LLM 세션이 단위를 순회하며 컨텍스트가 누적되는 모든 작업.
- **설계 스케치**: 스캐너를 우회하는 범용 입력, 예:
  `kn-estimate --units units.json` — `[{"id": "...", "group": "...", "w": 1234}, …]`.
  `group`이 컨트롤러 친화 묶음을 대체하고, 이후의 캘리브레이션·청크·벽·보고서
  로직은 수정 없이 동작한다. 캘리브레이션도 동일 — 그 작업 유형으로 파일럿
  2점을 실측하면 된다 (계수는 작업 유형·하네스마다 다르므로 재사용 불가).
- 현재는 미구현이다 — 이 절은 방향 기록이다.

## 7. FAQ

- **run_ledger.jsonl이 저장소 어디에 있나?** 없다 — 여러분이 만든다. 동봉 실물
  예시는 `results/campaign/*/run_ledger.jsonl`.
- **트랜스크립트는 자동으로 나오나?** Claude Code가 세션마다 자동 기록한다
  (`~/.claude/projects/…/<세션id>.jsonl`). 복사만 하면 된다.
- **run이 하나뿐이면?** 셀이 산출되지 않고 `kn-calibrate`가 비-0 exit로 실패한다
  (크기가 다른 2점 필요). 오류 메시지가 사유를 알려준다.
- **왜 게이트 실패 run을 버리나?** 조기 종료된 run은 비용이 실제보다 작아 계수를
  낙관적으로 오염시킨다. 실패율 자체는 라벨·모델 선택의 별도 판단 재료다
  (보고서의 haiku 각주가 그 예).
