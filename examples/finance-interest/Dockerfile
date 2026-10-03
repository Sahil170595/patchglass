# Bakes the synthetic finance repo at its base (buggy) commit, with git + pytest, ready for grading.
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir pytest==8.3.4
WORKDIR /app
COPY repo/ /app/
RUN git init -q \
 && git config user.email tb@example.com \
 && git config user.name taskbundle \
 && git add -A \
 && GIT_AUTHOR_DATE="2020-01-01T00:00:00Z" GIT_COMMITTER_DATE="2020-01-01T00:00:00Z" \
    git commit -qm base
