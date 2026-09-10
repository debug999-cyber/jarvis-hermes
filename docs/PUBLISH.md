# Публикация на GitHub

Репозиторий уже инициализирован (`git init`, ветка `main`, один коммит со всем проектом, `.gitignore`, CI в `.github/workflows/tests.yml`).
Осталось привязать удалённый репозиторий и отправить.

## Вариант A — через GitHub CLI (проще всего)
```bash
brew install gh && gh auth login          # один раз
cd jarvis-hermes
gh repo create jarvis-hermes --public --source=. --push --description "J.A.R.V.I.S. on Hermes Agent — голосовой ассистент для macOS с самообслуживаемой базой знаний"
```
(`--private` вместо `--public`, если не хотите публиковать.)

## Вариант B — через сайт
1. github.com → New repository → имя `jarvis-hermes`, **без** README/.gitignore/лицензии (они уже есть).
2. В терминале:
```bash
cd jarvis-hermes
git remote add origin git@github.com:<ваш-логин>/jarvis-hermes.git   # или https://github.com/<логин>/jarvis-hermes.git
git push -u origin main
```

## После пуша
- Вкладка **Actions** прогонит тесты на Ubuntu и macOS (Python 3.11/3.12).
- Проверьте, что в репозиторий не попал `.env` (он в `.gitignore`; ключей в проекте нет — только `config/.env.example` с пустыми значениями).
- В README замените при желании автора коммита: `git commit --amend --author="Имя <email>"` до пуша.
