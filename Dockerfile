# kn-estimator — 정적 스캔 도구 (LLM 호출 없음, stdlib 전용)
FROM python:3.12-alpine

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# 대상 프로젝트를 볼륨으로 마운트해 스캔한다:
#   docker run --rm -v "$PWD:/w" ghcr.io/baekchangjoon/kn-estimator /w --groups
ENTRYPOINT ["kn-estimate"]
