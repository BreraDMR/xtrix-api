# 🔧 Настройка GitHub Pages

## Проблема:
```
Get Pages site failed. Please verify that the repository has Pages enabled
```

## Решение:

### Шаг 1: Включите GitHub Pages

1. Зайдите на https://github.com/BooNaty/Xtrix
2. Перейдите в **Settings** (Настройки) → **Pages** (в левом меню)
3. В разделе **Source** (Источник):
   - Выберите **"GitHub Actions"**
   - Нажмите **Save**

### Шаг 2: Дождитесь деплоя

После включения GitHub Pages, при следующем push в `main` ветку:
- Workflow автоматически соберет фронтенд
- Задеплоит его на GitHub Pages
- Ваш сайт будет доступен по адресу: `https://boonaty.github.io/Xtrix/`

---

## ⚠️ Важно:

Если вы **НЕ** хотите использовать GitHub Pages:
- Можете просто удалить файл `.github/workflows/deploy.yml`
- Или оставить его - он будет пытаться деплоить, но не будет критичных ошибок

---

## 📝 После настройки:

После включения GitHub Pages, выполните еще раз push (или подождите следующего):

```powershell
git add .
git commit -m "Update"
git push
```

И проверьте вкладку **Actions** - деплой должен пройти успешно!

