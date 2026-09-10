#!/usr/bin/env bash
# Публикация JARVIS на GitHub одной командой.
# Использование: bash scripts/publish.sh            (логин и токен спросит)
#                GITHUB_TOKEN=ghp_… bash scripts/publish.sh
set -euo pipefail
cd "$(dirname "$0")/.."
LOGIN="${GITHUB_LOGIN:-debug999-cyber}"
REPO="${GITHUB_REPO:-jarvis-hermes}"
NAME="${GIT_NAME:-ERTGYKI}"
EMAIL="${GIT_EMAIL:-zdanovichmd01@gmail.com}"

echo "→ Публикуем в https://github.com/$LOGIN/$REPO"
if [[ -z "${GITHUB_TOKEN:-}" ]]; then
  echo "  Нужен Personal Access Token (classic) с правом repo:"
  echo "  https://github.com/settings/tokens/new?scopes=repo&description=jarvis-hermes"
  read -r -s -p "  Вставьте токен (ghp_…) и нажмите Enter: " GITHUB_TOKEN; echo
fi
[[ "$GITHUB_TOKEN" == ghp_* || "$GITHUB_TOKEN" == github_pat_* ]] || { echo "✖ Это не похоже на токен GitHub"; exit 1; }

# 1. автор коммитов
git config user.name "$NAME"; git config user.email "$EMAIL"
if git log --format='%an' | grep -qv "^$NAME$"; then
  echo "→ Переписываем автора коммитов на $NAME"
  git rebase -q -r --root --exec 'git commit -q --amend --no-edit --reset-author'
fi

# 2. создать репозиторий, если его нет (через API GitHub)
code=$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: token $GITHUB_TOKEN" "https://api.github.com/repos/$LOGIN/$REPO")
if [[ "$code" == "404" ]]; then
  echo "→ Создаём пустой репозиторий $LOGIN/$REPO"
  curl -s -f -o /dev/null -H "Authorization: token $GITHUB_TOKEN" https://api.github.com/user/repos \
    -d "{\"name\":\"$REPO\",\"description\":\"J.A.R.V.I.S. on Hermes Agent — голосовой ассистент для macOS с самообслуживаемой базой знаний\",\"private\":${PRIVATE:-false}}" \
    || { echo "✖ Не удалось создать репозиторий (токен без права repo? логин не $LOGIN?)"; exit 1; }
elif [[ "$code" == "200" ]]; then
  echo "→ Репозиторий уже существует, будем пушить в него"
else
  echo "✖ GitHub ответил $code — проверьте токен (401 = неверный/просроченный)"; exit 1
fi

# 3. remote и push (токен не сохраняется в .git/config)
git remote remove origin 2>/dev/null || true
git remote add origin "https://github.com/$LOGIN/$REPO.git"
git branch -M main
git -c credential.helper= -c "http.extraheader=Authorization: Basic $(printf '%s:%s' "$LOGIN" "$GITHUB_TOKEN" | base64)" \
    push -u origin main
echo
echo "✔ Готово: https://github.com/$LOGIN/$REPO"
echo "  Тесты запустятся автоматически: https://github.com/$LOGIN/$REPO/actions"
