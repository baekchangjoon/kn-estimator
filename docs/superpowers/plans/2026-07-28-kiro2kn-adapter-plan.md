# kiro2kn 어댑터 구현 계획 — Kiro CLI 세션을 kn-calibrate 입력으로 (v2, 3-리뷰 반영)

> 목적: Kiro CLI로 실행한 파일럿 세션을 kn-calibrate가 읽는 계약 형식(트랜스크립트
> + 원장 라인)으로 변환하는 어댑터. 배경: [CALIBRATION.md](../../CALIBRATION.md) §5.
>
> 표기: 가독성을 위해 `kiro2kn`은 `python research/adapters/kiro2kn.py`의 별칭으로
> 적는다 — 콘솔 스크립트 엔트리포인트는 만들지 않는다(D6).
>
> 검증 범위 한정: 이 계획의 실측 검증(2026-07-28, 이 머신)은 **pct-폴백 경로(D3-②)
> 만** 실데이터로 확인했다(실제 30턴 대화 → τ=30, S0=13,790, cmax=68,075 →
> `_turn_stats` 동일 판독). 토큰 필드 직접 매핑 경로(D3-①)는 현 버전 데이터가 전부
> null이라 합성 데이터로만 검증된다(§5 리스크).

## 0. 실데이터로 확정한 사실 (이 계획의 전제)

| 항목 | 확인 결과 |
|---|---|
| 정본 저장소 | `~/Library/Application Support/kiro-cli/data.sqlite3` → `conversations_v2(key=cwd, conversation_id, value=대화 JSON, created_at, updated_at)` — 시각은 epoch ms |
| conversation_id 형식 | UUID 36자 (목록 표시는 8자로 축약, 조회는 접두 매칭 — §1 S2) |
| 턴 단위 | `value.history[i] = {user, assistant, request_metadata}` — 배열 인덱스일 뿐 메시지 고유 id는 없다 |
| 컨텍스트 | `request_metadata`의 토큰 필드 5종은 스키마에 있으나 현 버전 데이터에서 전부 null; `context_usage_percentage`는 전 턴 존재, `model_info.context_window_tokens`(예: 1,000,000)와 곱해 복원 |
| out 재료 | `history[i].assistant`에 응답 본문과 `tool_uses`(이름·인자) 전부 존재 → 바이트/4 근사 가능 |
| `~/.kiro/sessions/cli/<id>.jsonl` | Prompt/AssistantMessage 이벤트 로그 — request_metadata 없음(컨텍스트 복원 불가). out 근사 전용 폴백 |

## 1. 사용자 시나리오 (수용 기준의 원천)

**S1 — 파일럿 실행**: Kiro CLI로 그룹 생성 세션을 돌린다 (CALIBRATION.md §3
템플릿, 크기가 다른 그룹 2개 이상). 변환은 **세션 종료 후**에 한다(D6).

**S2 — 세션 식별**:

```bash
$ kiro2kn --list --cwd ~/work/my-backend        # cwd로 좁히고 최신순
  c7719363  2026-07-28 14:02→14:04  30턴  "다음 엔드포인트에 대한 블랙박스 API 테스트를…"
```

목록 열: conversation_id 앞 8자, 시작→종료 시각(created_at→updated_at), 턴 수,
첫 프롬프트 미리보기. `--cwd` 미지정 시 전체 목록. 조회 인자는 **접두 매칭**이며,
접두가 2개 이상의 대화와 충돌하면 후보를 나열하고 비-0 exit한다(조용한 선택 금지).

**S3 — 변환+기록**:

```bash
$ kiro2kn c7719363 --variant flat_template_sonnet --n 1 --rep 1 --gate pass \
          --runs-dir runs/ --ledger run_ledger.jsonl [--cost 0.42]
runs/myback_flat_template_sonnet-n1-r1/transcript.jsonl 생성 (30턴)
run_ledger.jsonl에 1줄 추가 (harness/out_approx/wall_s 포함)
```

run_id 기본 `<cwd basename>_<variant>-n<n>-r<rep>` (`--run-id`로 오버라이드).
`--gate`는 사용자가 판정한다(컴파일 기준 — 어댑터는 판정하지 않는다).

**S4 — 캘리브레이션**: 기존 흐름 그대로 `kn-calibrate --ledger … --runs …`.

## 2. 설계 결정

**D1 — kn-calibrate는 하네스를 구분하지 않는다.** 구분은 어댑터 단계에서 끝나고
입력 계약(트랜스크립트+원장 스키마)은 불변이다. calibrate에 하네스별 파서 분기를
넣는 대안은 파서 증식·결합을 낳으므로 기각.

**D2 — 혼합 방지는 원장 메타로. 결측은 "claude-code"의 암묵 별칭이다.** 어댑터는
원장 라인에 `"harness": "kiro-cli"`를 기입하고, CALIBRATION.md의 Claude Code
스니펫도 `"harness": "claude-code"`를 기입하도록 갱신한다. kn-calibrate의 판정
규칙: **셀 내 harness 값 집합(결측은 "claude-code"로 정규화)에 distinct 값이
2종 이상이면 경고**(차단 아님). 결측=claude-code 별칭인 근거: 이 저장소의 기존
원장(campaign)은 전부 Claude Code 실측이다. 따라서 구 원장+Kiro run 혼합은
경고되고, 구 원장+새 claude-code 명시 run 혼합은 경고되지 않는다.

**D3 — 컨텍스트 복원 2단 폴백 + 턴 고유 id 합성.** ① `request_metadata` 토큰
필드가 채워져 있으면 그대로 계약 필드에 매핑(cache_read→cache_read,
uncached→input, cache_write→cache_creation). ② 아니면
`context_usage_percentage/100 × context_window_tokens`를
`cache_read_input_tokens`에 기입, 나머지 0 (복원 오차 ±수십 토큰 — 2점 fit에
무시 가능). 트랜스크립트 각 줄의 `message.id`는 **`f"{conversation_id}-{i}"`**(i=턴
인덱스)로 합성한다 — `_turn_stats`가 id로 dedup하므로 고유하지 않으면 τ가
붕괴한다.

**D4 — out 채널은 sqlite에서 근사, 전파는 calibration.json까지.**
`history[i].assistant`의 응답 본문+`tool_uses` 직렬화 바이트/4를 run 합계
`output_tokens`로 쓴다(사고 토큰 미포함 근사). 원장 라인에 `"out_approx": true`를
표기하고, kn-calibrate는 셀에 `out_approx` 필드로 기록한다 — **전파 범위는
calibration.json의 셀까지다**. 보고서(kn-report.md) 노출은 이번 스코프의
비목표다: 기존 `env_split_approx`도 셀까지만 기록되고 보고서에는 노출되지 않는
동일한 상태이며, 두 플래그의 보고서 배선은 별도 후속 과제로 추적한다. 토큰
필드가 채워진 데이터면 정확값을 자동 사용하고 `out_approx: false`.

**D5 — cost_usd는 사용자 책임.** Kiro는 크레딧 과금이라 어댑터가 계산하지
않는다. `--cost <usd환산>` 입력을 받고, 미지정 시 0 기입 + 경고 출력(상대 비교
전용임을 명시). cost=0 run만 있는 셀의 분산 밴드는 기존 기본 밴드 동작 유지.

**D6 — 위치·실행·안전.** `research/adapters/kiro2kn.py`, stdlib만(sqlite3 포함),
테스트는 **`tests/test_kiro2kn.py`**(회귀 스위트 `pytest tests/`에 포함). sqlite는
**읽기 전용 URI(`file:…?mode=ro`)로 연다** — Kiro CLI가 세션 중 쓰기 락을 쥘 수
있으므로 변환은 세션 종료 후를 전제하고, 락 충돌 시 재시도 안내와 함께 비-0
exit한다. 기본 DB 경로는 §0의 macOS 경로이되 `--db <경로>`로 오버라이드 가능.
스키마 불일치(키 부재)는 결측 키를 지목하며 시끄럽게 실패한다.

**D7 — wall_s는 sqlite 시각에서 산출한다.** 원장 필수 필드 `wall_s`(calibrate가
무조건 인덱싱, `latency_s_per_turn`의 분자)는 **`(updated_at − created_at)/1000`
반올림**으로 기입한다. 세션을 중간에 쉬었다면 벽시계가 과대될 수 있다 — 파일럿은
단일 연속 세션으로 돌리는 것을 전제로 §5 리스크에 기록.

**D8 — sessions-jsonl 폴백은 원장 전용이다.** sqlite에서 대화가 정리·삭제된 경우
`--sessions-jsonl <경로>`는 AssistantMessage 이벤트로 out 근사와 wall_s(이벤트
timestamp 차)만 계산해 **원장 라인만 만든다 — 트랜스크립트는 생성하지 않는다**
(request_metadata가 없어 컨텍스트 복원 불가). 트랜스크립트 없는 run은
kn-calibrate의 기존 `missing_transcript` 경로가 계수에서 제외하고 사유를
병기하므로 새 메커니즘이 필요 없다. 즉 이 폴백의 용도는 "그 run의 비용·출력
기록 보존"이지 계수 기여가 아니다.

## 3. 요구사항 매트릭스 (완료 정의의 분모)

> 전역 워크플로의 독립 요구사항명세 문서 규칙에 대해: 본 건은 어댑터 스크립트
> 1개+경고 1건 규모라 비례성 조항(최소 매트릭스 내장)을 적용한다 — 별도
> requirements 파일 대신 이 표가 그 역할을 한다.

| REQ | 요구 (Given-When-Then) | 수용 테스트 (tests/test_kiro2kn.py) |
|---|---|---|
| REQ-001a | 합성 sqlite에서 `--list --cwd` 실행 시 cwd 필터·최신순·8자 id·턴수·첫 프롬프트가 출력된다 | `test_list_filters_by_cwd` |
| REQ-001b | `--cwd` 없이 `--list` 실행 시 전체 대화가 나열된다 | `test_list_without_cwd_shows_all` |
| REQ-002 | pct-폴백 대화 변환 시 턴별 컨텍스트가 pct×window와 일치하고 `_turn_stats`의 **τ == len(history)**, S0·cmax가 정확하다 (id 합성 검증 포함) | `test_convert_pct_fallback_roundtrip` |
| REQ-003 | 토큰 필드가 채워진 대화는 pct가 아니라 실제 토큰 필드가 매핑되고 `out_approx: false`가 된다 | `test_convert_prefers_real_token_fields` |
| REQ-004 | 변환 시 원장 라인에 harness=kiro-cli, `out_approx: true`(근사 시), **wall_s=(updated_at−created_at)/1000**가 기입된다 | `test_ledger_line_fields` |
| REQ-005 | 같은 셀에서 (a) kiro-cli+claude-code 명시 혼합 → 경고, (b) kiro-cli+결측 혼합 → 경고, (c) claude-code 명시+결측 혼합 → 경고 없음 | `test_calibrate_mixed_harness_warnings` |
| REQ-006 | 스키마 불일치(history/pct/window 키 부재) 시 결측 키를 지목하며 비-0 exit | `test_schema_mismatch_fails_loudly` |
| REQ-007 | **E2E**: 합성 sqlite의 크기가 다른 run 2개(n1/n3) 변환 → kn-calibrate가 셀 계수 산출(out_approx 포함) → `kn-estimate --calibration` 완주 | `test_kiro_pilot_loop_end_to_end` |
| REQ-008 | id 접두가 2개 이상과 충돌하면 후보를 나열하고 비-0 exit | `test_ambiguous_prefix_fails_with_candidates` |
| REQ-009 | `--cost` 미지정 시 0 기입 + 경고 출력 | `test_cost_default_zero_warns` |
| REQ-010 | `--run-id` 지정 시 기본 명명 대신 그 값이 쓰인다 | `test_run_id_override` |
| REQ-011 | `--sessions-jsonl` 폴백은 원장 라인만 만들고 트랜스크립트를 만들지 않으며, 이후 kn-calibrate에서 해당 run이 missing_transcript로 제외된다 | `test_sessions_jsonl_fallback_ledger_only` |

완료 정의 = REQ 12건 수용 테스트 green + 기존 스위트 무회귀(현 기준 67 passed /
14 skipped, SUT 포함 80 passed / 1 skipped) + CALIBRATION.md §5 어댑터 연결 +
이 계획과 실물의 동기화.

## 4. 구현 단계 (double-loop TDD — E2E 먼저)

1. 합성 sqlite 픽스처 빌더(테스트 헬퍼) — §0 스키마 그대로 conversations_v2 구성.
2. **RED (outer loop): REQ-007 E2E를 먼저 작성** — 어댑터가 없으므로 red.
3. RED→GREEN (inner loop): REQ-001a/b, 002~004, 006, 008~010 단위 TDD로 어댑터 구현.
4. RED→GREEN: REQ-005 — calibrate에 harness 정규화·혼합 경고 추가.
5. RED→GREEN: REQ-011 — sessions-jsonl 폴백.
6. REQ-007 E2E green 확인(outer loop 종료).
7. CALIBRATION.md §5 갱신(어댑터 사용법 + Claude Code 스니펫에 harness 필드).
8. 게이트: 스펙 준수+품질 리뷰 → 전체 스위트 → PR → CI green → 리베이스 머지.

## 5. 리스크와 한계

- **Kiro 버전 의존**: conversations_v2는 비공개 내부 형식 — REQ-006의 시끄러운
  실패가 방어선, §0 표가 기준 버전 기록.
- **D3-① 경로는 합성 검증뿐**: 현 버전 실데이터의 토큰 필드가 전부 null이라 실측
  검증은 pct-폴백만 됐다. Kiro가 토큰 필드를 채우기 시작하면 ①경로는 그때 첫
  실전을 치른다.
- **out 근사 편향**: 사고 토큰 미포함 → out 계수 과소 가능. `out_approx`는
  calibration.json까지만 전파(보고서 배선은 env_split_approx와 함께 별도 후속).
- **wall_s 과대 가능**: 세션 중 휴지 시간이 포함된다 — 파일럿은 단일 연속 세션
  전제.
- **절대 비용 비교 불가**: 크레딧 과금(D5). Kiro 셀은 상대 비교·플랜 용도.
- **sqlite 동시 접근**: 세션 진행 중 변환은 락 충돌 가능 — ro 연결 + 종료 후 변환
  전제(D6).
- **세션 파일↔대화 대응 미확정**: `~/.kiro/sessions/cli`의 세션 id와
  conversation_id의 대응은 표본 부족으로 미확정 — 폴백(D8)은 파일 직접 지정으로
  우회.
