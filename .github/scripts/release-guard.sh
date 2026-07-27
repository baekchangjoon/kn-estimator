#!/usr/bin/env bash
# v* 태그·릴리스가 release.yml 계약을 지키는지 검증하는 탐지 컨트롤.
# 검사: ① 태그 커밋의 pyproject.toml 버전 == 태그 버전
#       ② (REQUIRE_RELEASE=1) 릴리스 존재 + 자산 = sdist/wheel + 각 .sha256, 잉여 없음
#       ③ 자산 체크섬 실검증 (다운로드 후 sha256sum -c)
# 필요 env: TAG, GH_REPO, GH_TOKEN, REQUIRE_RELEASE(0|1). 태그 소스 트리에서 실행.
set -euo pipefail

if [[ ! "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+([._+-][A-Za-z0-9._+-]*)?$ ]]; then
  echo "::error::'$TAG'는 v<semver> 태그가 아니다."
  exit 1
fi
VERSION="${TAG#v}"

SOURCE_VERSION=$(python3 - <<'PY'
try:
    import tomllib
    v = tomllib.load(open("pyproject.toml", "rb"))["project"]["version"]
except ModuleNotFoundError:   # python<3.11 폴백 (로컬 실행 대비)
    import re
    v = re.search(r'^version\s*=\s*"([^"]+)"',
                  open("pyproject.toml").read(), re.M).group(1)
print(v)
PY
)
if [ "$SOURCE_VERSION" != "$VERSION" ]; then
  echo "::error::태그 $TAG 의 pyproject.toml 버전($SOURCE_VERSION)이 태그 버전($VERSION)과 다르다 — 이 태그를 클론해 빌드하면 릴리스와 다른 버전이 나온다."
  exit 1
fi
echo "소스 버전 일치: $VERSION"

if [ "${REQUIRE_RELEASE:-0}" != "1" ]; then
  echo "릴리스 자산 검사는 생략 (태그 push 이벤트 — release.yml 생성과 레이스 가능)."
  exit 0
fi

EXPECTED_SDIST="kn_estimator-${VERSION}.tar.gz"
EXPECTED_WHEEL="kn_estimator-${VERSION}-py3-none-any.whl"
ASSETS=$(gh release view "$TAG" --repo "$GH_REPO" --json assets -q '.assets[].name' | sort)
if [ -z "$ASSETS" ]; then
  echo "::error::릴리스 $TAG 에 자산이 없다 (기대: sdist/wheel + 각 .sha256)."
  exit 1
fi
EXPECTED=$(printf '%s\n' "$EXPECTED_SDIST" "$EXPECTED_SDIST.sha256" "$EXPECTED_WHEEL" "$EXPECTED_WHEEL.sha256" | sort)
if [ "$ASSETS" != "$EXPECTED" ]; then
  echo "::error::릴리스 $TAG 자산 세트 불일치."
  echo "기대:"; echo "$EXPECTED"
  echo "실제:"; echo "$ASSETS"
  exit 1
fi

TMP=$(mktemp -d)
gh release download "$TAG" --repo "$GH_REPO" --dir "$TMP"
( cd "$TMP" && sha256sum -c ./*.sha256 )
echo "릴리스 $TAG: 자산 세트·체크섬 검증 통과."
