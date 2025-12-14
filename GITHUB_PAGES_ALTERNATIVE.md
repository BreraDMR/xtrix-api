# Альтернативный способ настройки GitHub Pages

## Вариант 1: Использовать ветку gh-pages (если GitHub Actions недоступен)

1. Зайдите в **Settings** → **Pages**
2. В разделе **Source** выберите **"Deploy from a branch"**
3. Выберите:
   - **Branch**: `gh-pages`
   - **Folder**: `/ (root)`
4. Нажмите **Save**

Затем удалите `.github/workflows/deploy.yml` и используйте простой деплой через npm скрипт (см. ниже).

---

## Вариант 2: Отключить автоматический деплой (рекомендуется, если GitHub Actions не работает)

Если GitHub Pages не работает с Actions, просто **удалите файл** `.github/workflows/deploy.yml`:

```powershell
git rm .github/workflows/deploy.yml
git commit -m "Remove GitHub Pages deployment"
git push
```

И используйте только для показа кода на GitHub.

---

## Вариант 3: Простой деплой через gh-pages (работает всегда)

Если хотите задеплоить вручную:

1. **Установите gh-pages:**
```powershell
cd frontend
npm install --save-dev gh-pages
```

2. **Добавьте в `frontend/package.json`:**
```json
{
  "scripts": {
    "deploy": "npm run build && gh-pages -d dist"
  },
  "homepage": "https://boonaty.github.io/Xtrix/"
}
```

3. **Задеплойте:**
```powershell
cd frontend
npm run deploy
```

4. **В настройках GitHub Pages:**
   - **Source**: `Deploy from a branch`
   - **Branch**: `gh-pages` → `/ (root)`

---

## Самый простой вариант (только код, без сайта):

Если вам нужен только код на GitHub для проверки:

1. **Удалите deploy workflow:**
```powershell
git rm .github/workflows/deploy.yml
git commit -m "Remove deploy workflow"
git push
```

2. **Просто поделитесь ссылкой на репозиторий:**
```
https://github.com/BooNaty/Xtrix
```

Проверяющий сможет склонировать репозиторий и запустить локально.

