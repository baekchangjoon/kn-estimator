# kn-estimator 세션 핸드오프

이 저장소는 원 실험 저장소의 `tools/kn_estimator/`를 독립 도구로 분리한 것이다
(2026-07-16 시딩 → 분리·개선 완료). 2026-07-27에 **기준 SUT·동봉 데이터를 공개
가능한 캠페인 자산(petclinic·tainted-spring)으로 전면 교체**했다 — 초기
캘리브레이션에 쓰인 비공개 레거시 SUT의 명칭·원자료는 저장소에서 제거·익명화됐다
(작업 트리 기준; **git 히스토리에는 남아 있다**).

## 1. kn-estimator가 무엇인가

테스트 생성 착수 **전에** 대상 Spring 프로젝트를 정적 스캔(LLM 호출 없음, 수 초)해
N(엔드포인트 수)·w_i(EP별 작업량)·청크 플랜(FFD)·예상 비용/시간(모드×모델 매트릭스)을
산출. 사용법: `README.md`, 상세: `docs/GUIDE.md`,
전체 정리: `docs/2026-07-16-kn-estimator-overview.md`.

## 2. 현행 기준선 (2026-07-27, 검증 완료 스모크)

```bash
# SUT: spring-petclinic 포크(비공개) fe8128079e12fd1a47b4c2757a4b51a12fa3adf2 클론을
# ./petclinic 에 두거나 KN_SUT로 지정
python3.12 -m venv .venv && .venv/bin/pip install -e '.[test]'
.venv/bin/kn-estimate petclinic --mode template --model sonnet
# → N=18 chunks=3 k_avg=6.0 est=$21.18, petclinic/.kn/{kn-report.md,kn-plan.json}
#   골든: kn-plan.json sha256 e3f96f19ce5ab11f…, kn-report.md 18427390718262f6…
#   (PYTHONHASHSEED 무관 바이트 일치 — 결정성 검증됨)
.venv/bin/python -m pytest tests/   # SUT 없이 67 passed, SUT 있으면 80 passed
```

| 자산 | 위치 | 유래 |
|---|---|---|
| 도구 6모듈+테스트+GUIDE | `src/kn_estimator/`, `tests/`, `docs/` | 분리 완료(2026-07-16) 후 개선 누적 |
| 동봉 캘리브레이션(기본) | `src/kn_estimator/data/calibration.json` | tainted-spring-auth-user 캠페인 실측 (3셀: template/sonnet·template/haiku·flat/sonnet) |
| 동봉 캘리브레이션(대안) | `data/calibration-{petclinic,community}.json` | 캠페인 실측 — `--calibration`으로 선택 |
| 캘리브레이션 원장(정본) | `results/campaign/{petclinic,auth-user,community}/` | 2026-07 캠페인 54 run |
| SUT | `petclinic/` (gitignore) | spring-petclinic 포크, 상기 커밋 핀 |

## 3. 이력 요약

- **2026-07-16** 분리 완료: harness 결합 제거, 정식 패키지화, 비결정성 수리(BFS+정렬).
- **2026-07-20** 인벤토리 재현율 수리 3건 + JPA 1-hop, 3개 프로젝트 캘리브레이션
  캠페인(54 run) — `docs/2026-07-20-multi-project-calibration-campaign.md`.
- **2026-07-26** 이론-구현 감사 격차 수리(모델별 w_hard 캡, skipped_cells, XML dedup,
  K\*·비용곡선 병기, --groups, 파일럿 고지) — PR #2~#4.
- **2026-07-27** SUT 교체: 동봉 캘리브레이션을 캠페인(auth-user) 실측으로 교체,
  `W_SOFT_DEFAULT` 330K 재산정(현세대 env≈178K가 구 180K 벽을 채우는 F7 퇴화 방지),
  레거시 SUT 원자료 제거·명칭 익명화, 테스트·문서 petclinic 재기준선.

## 4. 미결·주의

- **git 히스토리 재작성 완료 (2026-07-27)** — `git filter-repo`로 전 커밋에서 구 SUT
  원자료(원장·트랜스크립트·rv3·per_ep_covariate.py)를 경로 삭제하고 명칭을 블롭 수준
  치환한 뒤 force-push했다 (tip 트리 바이트 불변 검증, 전 커밋 grep 0건, 로컬 백업
  번들 `~/kn-estimator-prerewrite-20260727.bundle`). **잔존**: GitHub의 `refs/pull/1~6`
  (PR 페이지)이 재작성 전 커밋을 계속 참조한다 — 이는 push로 지울 수 없고, 완전
  제거는 GitHub Support의 GC 요청 또는 저장소 삭제·재생성이 필요하다.
- 동봉 캘리브레이션은 여전히 **단일 프로젝트(tainted-spring-auth-user) 실측**이다 —
  절대 USD 비보증 한계는 동일하고, CLI가 파일럿 절차를 자동 고지한다.
- opus 셀은 캠페인에서 미실측 — 매트릭스에 `insufficient_calibration`으로 나온다.
  opus 추정이 필요하면 파일럿 실측이 선행돼야 한다.

## 5. 작업 규약

- 실행: `python3.12` (시스템 python3는 3.9라 부족할 수 있음).
- 개발 워크플로우는 `~/.claude/dev-workflow.md` 전역 규칙을 따른다 — 특히: 기능 작업은
  브랜치+워크트리, TDD, PR 전 3게이트, CI green 후 리베이스 머지.
